import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import radar


class RadarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = radar.read_json(radar.ROOT / "config.json")
        cls.fixture = radar.read_json(radar.ROOT / "tests" / "fixture_assessment.json")

    def test_fixture_is_valid(self):
        self.assertEqual(radar.validate_assessment(self.fixture, self.config), [])

    def test_validator_fails_closed_on_wrong_json_types(self):
        errors = radar.validate_assessment({"items": ["not-an-object"]}, self.config)
        self.assertIn("items[0] must be an object", errors)
        self.assertGreater(len(errors), 1)

    def test_overall_score_is_recomputed_by_program(self):
        payload = json.loads(json.dumps(self.fixture, ensure_ascii=False))
        payload["items"][0]["overall_score"] = 0
        radar.normalize_assessment_scores(payload)
        self.assertEqual(payload["items"][0]["overall_score"], 68.0)
        self.assertEqual(radar.validate_assessment(payload, self.config), [])

    def test_render_contains_required_sections(self):
        rendered = radar.render_report(self.fixture)
        self.assertIn("AI GitHub Radar", rendered)
        self.assertIn("今日项目", rendered)
        self.assertIn("产品经理思考题", rendered)

    def test_utf8_chunk_size(self):
        text = ("Agent Skill 是可复用能力。\n\n" * 2000).strip()
        chunks = radar.byte_chunks(text, max_bytes=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 1000 for chunk in chunks))
        self.assertEqual("\n\n".join(chunks), text)

    def test_signature_is_stable(self):
        first = radar.feishu_signature("secret", 1234567890)
        second = radar.feishu_signature("secret", 1234567890)
        self.assertEqual(first, second)

    def test_dry_run_does_not_need_webhook(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "test.md"
            report.write_text("# test\n", encoding="utf-8")
            old_data_dir = radar.DATA_DIR
            try:
                radar.DATA_DIR = Path(directory) / "data"
                result = radar.deliver(report, dry_run=True)
                self.assertTrue(result[0]["dry_run"])
                payloads = json.loads(
                    (radar.DATA_DIR / "feishu_payload_preview.json").read_text(encoding="utf-8")
                )
                self.assertEqual(payloads[0]["msg_type"], "interactive")
            finally:
                radar.DATA_DIR = old_data_dir

    def test_configured_timezone_controls_report_date(self):
        fixed = datetime(2026, 8, 7, 23, 40, tzinfo=UTC)
        with patch.object(radar, "now_utc", return_value=fixed):
            self.assertEqual(radar.local_now(self.config).date().isoformat(), "2026-08-08")

    def test_structured_output_schema_is_strict(self):
        schema = radar.assessment_json_schema(self.config)
        self.assertFalse(schema["additionalProperties"])
        item_schema = schema["properties"]["items"]["items"]
        self.assertFalse(item_schema["additionalProperties"])
        self.assertEqual(
            set(item_schema["properties"]["scores"]["required"]), radar.SCORE_KEYS
        )

    def test_extract_response_text_from_rest_shape(self):
        response = {
            "output": [
                {"content": [{"type": "output_text", "text": '{"ok":true}'}]}
            ]
        }
        self.assertEqual(radar.extract_response_text(response), '{"ok":true}')

    def test_openai_request_uses_structured_outputs(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "id": "resp_test",
                        "status": "completed",
                        "model": "gpt-test",
                        "output_text": json.dumps(self_payload, ensure_ascii=False),
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                    }
                ).encode("utf-8")

        self_payload = self.fixture
        client = radar.OpenAIResponsesClient("test-key", "gpt-test")
        with patch.object(radar.urllib.request, "urlopen", return_value=FakeResponse()) as call:
            assessment, metadata = client.create_assessment(
                {"date": "2026-08-04", "candidates": []}, self.config, "test instruction"
            )
        request = call.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertFalse(body["store"])
        self.assertEqual(assessment["date"], self.fixture["date"])
        self.assertEqual(metadata["response_id"], "resp_test")

    def test_deepseek_request_uses_json_output(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "id": "deepseek_resp_test",
                        "model": "deepseek-v4-flash",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": json.dumps(self_payload, ensure_ascii=False)
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    }
                ).encode("utf-8")

        self_payload = self.fixture
        client = radar.DeepSeekChatClient("test-key", max_attempts=1)
        with patch.object(radar.urllib.request, "urlopen", return_value=FakeResponse()) as call:
            assessment, metadata = client.create_assessment(
                {"date": "2026-08-04", "candidates": []}, self.config, "test JSON instruction"
            )
        request = call.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertIn("JSON Schema", body["messages"][0]["content"])
        self.assertEqual(assessment["date"], self.fixture["date"])
        self.assertEqual(metadata["provider"], "deepseek")

    def test_deepseek_retries_an_empty_json_response(self):
        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "id": "retry-test",
                        "model": "deepseek-v4-flash",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": self.content},
                            }
                        ],
                    }
                ).encode("utf-8")

        responses = [FakeResponse(""), FakeResponse(json.dumps(self.fixture, ensure_ascii=False))]
        client = radar.DeepSeekChatClient("test-key", max_attempts=2)
        with patch.object(
            radar.urllib.request, "urlopen", side_effect=responses
        ) as call, patch.object(radar.time, "sleep"):
            assessment, _metadata = client.create_assessment(
                {"date": "2026-08-04", "candidates": []}, self.config, "JSON"
            )
        self.assertEqual(call.call_count, 2)
        self.assertEqual(assessment["date"], self.fixture["date"])

    def test_deepseek_is_the_default_provider(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
            client = radar.build_ai_client(self.config)
        self.assertIsInstance(client, radar.DeepSeekChatClient)
        self.assertEqual(client.model, "deepseek-v4-flash")

    def test_delivery_receipt_prevents_duplicate_send(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "2026-08-08.md"
            report.write_text("# daily\n", encoding="utf-8")
            old_data_dir = radar.DATA_DIR
            radar.DATA_DIR = Path(directory) / "data"
            try:
                with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": "https://example.test"}), patch.object(
                    radar, "post_feishu", return_value={"code": 0}
                ) as post:
                    first = radar.deliver(report)
                    second = radar.deliver(report)
                self.assertEqual(first[0]["code"], 0)
                self.assertTrue(second[0]["skipped"])
                self.assertEqual(post.call_count, 1)
            finally:
                radar.DATA_DIR = old_data_dir

    def test_run_daily_orchestrates_all_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidates.json"
            candidate_path.write_text('{"date":"2026-08-04"}', encoding="utf-8")
            assessment_path = root / "assessment.json"
            assessment_path.write_text(
                json.dumps(self.fixture, ensure_ascii=False), encoding="utf-8"
            )
            old_reports_dir = radar.REPORTS_DIR
            radar.REPORTS_DIR = root / "reports"
            try:
                with patch.object(radar, "collect", return_value=candidate_path), patch.object(
                    radar,
                    "assess_candidates",
                    return_value=(assessment_path, {"model": "gpt-test", "response_id": "resp"}),
                ), patch.object(radar, "deliver", return_value=[{"code": 0}]) as delivery:
                    result = radar.run_daily(self.config)
                self.assertTrue(Path(result["report"]).exists())
                self.assertEqual(result["model"], "gpt-test")
                delivery.assert_called_once()
            finally:
                radar.REPORTS_DIR = old_reports_dir


if __name__ == "__main__":
    unittest.main()
