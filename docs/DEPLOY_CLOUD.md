# GitHub Actions 云端部署手册

部署完成后，即使 Codex/ChatGPT 桌面应用未打开，只要 GitHub Actions 可用，工作流仍会在云端运行；你的电脑也不需要开机。

## 前置条件

- 一个用于存放本项目的 GitHub 仓库。
- 可调用 Responses API 的 OpenAI API Key。ChatGPT 订阅与 API 账单是两个独立体系。
- 已创建的飞书自定义机器人 Webhook；签名校验密钥可选但建议启用。

任何密钥都不要发在聊天里，也不要写进 `.env.example` 或提交历史。

## 部署步骤

1. 把 `ai-github-radar` 目录作为仓库根目录推送到 GitHub，确保 `.github/workflows/daily-radar.yml` 位于仓库根下。
2. 在 GitHub 仓库进入 `Settings → Secrets and variables → Actions`。
3. 添加 Repository secrets：
   - `OPENAI_API_KEY`
   - `FEISHU_WEBHOOK_URL`
   - `FEISHU_SIGNING_SECRET`（若机器人启用签名）
4. 添加 Repository variables（可选）：
   - `OPENAI_MODEL=gpt-5.6-terra`
   - `OPENAI_REASONING_EFFORT=medium`
5. 进入 `Actions → Daily AI GitHub Radar → Run workflow`，做第一次手动运行。
6. 同时检查：Actions 全部步骤变绿、飞书收到日报、仓库出现当日日报与更新后的历史文件。
7. 连续观察 2 天，确认定时运行和 Star 增量正常，再停用本地 Codex 07:40 自动化，避免双重推送。

## 验收清单

- [ ] 单元测试通过。
- [ ] 候选采集成功，未触发 GitHub API 限流。
- [ ] OpenAI 返回严格 JSON，且业务校验通过。
- [ ] 飞书只收到一次，中文与链接正常。
- [ ] `reports/YYYY-MM-DD.md` 已归档。
- [ ] `data/repo_history.json` 和 `data/delivery_receipts.json` 已更新。
- [ ] Actions 日志没有出现任何密钥。

## 常见失败

### OpenAI API 401/403

检查 Key、API 项目权限和账单；不要把完整错误中的敏感内容复制到公开 Issue。

### GitHub API rate limited

工作流自动使用仓库的 `GITHUB_TOKEN`。如果查询量继续增长，应减少搜索或加缓存，不要盲目并发。

### 飞书拒绝消息

检查 Webhook 是否有效、签名开关和 `FEISHU_SIGNING_SECRET` 是否一致。可先在本地执行 `python radar.py deliver reports/test.md --dry-run` 检查卡片结构。

### 定时任务没跑

定时工作流只运行默认分支最新提交；平台繁忙时可能延迟。公开仓库长期无活动时，计划任务也可能被自动停用。先用手动触发区分“工作流配置错误”和“调度问题”。

### 重跑没有再次发送

这是防重复机制生效。同日期同内容默认跳过；确实需要重发时使用：

```powershell
python radar.py deliver reports/YYYY-MM-DD.md --force
```

## 成本控制

默认只把前 12 个候选和每个最多 2500 字符的 README 摘要发给模型。先记录 7 天的 `usage`，再比较 `gpt-5.6-terra` 与成本更低模型；只有质量评测不下降时才切换。不要仅凭单次主观感觉选择模型。
