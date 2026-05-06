# AI 招聘工作台

本项目是 BOSS 招聘工作台的本地桌面 MVP。

## 启动方式

```bash
./run_app.sh
```

也可以双击：

```text
run_app.command
```

## 配置文件

项目根目录下已经提供了配置文件：

- [app_config.json](/Users/weiyifeng/ai-boss/source_share_clean_20260506/app_config.json)

后续修改飞书凭证、飞书云文档参数、Dify 网关地址、Token 限额等，只需要改这个文件，不需要改代码。

说明：

- 开发态直接读取项目根目录下的 `app_config.json`
- Windows 安装版会把实际运行配置写到 `%APPDATA%\AIBossWorkbench\app_config.json`
- Windows 在线更新也通过这个配置文件控制，关键字段是 `windows_update_manifest_url`
- 你提供的飞书人员额度表 `app_token` 和 `table_id` 已经预填进配置文件

## 当前能力

- 打开应用前强制飞书认证，支持通过飞书 OAuth 登录后再进入工作台。
- 左侧原生招聘控制台，右侧嵌入 BOSS 受控浏览器。
- 支持岗位切换、岗位配置保存、简历目录配置。
- 支持投递人选扫描配置：开始序号、结束序号、消息状态。
- 支持主动找人配置：推荐牛人入口、扫描数量、自动打招呼开关、分数阈值。
- 扫描时支持停止请求，停止会在当前候选人处理完成后生效。
- 候选人池按岗位隔离，支持最近获取/评分排序、清空当前岗位、查看、索要简历、淘汰。
- 本地 SQLite 持久化：岗位、候选人、扫描日志、动作日志。
- 记录 AI 调用 token 使用量，支持本地逐次记账、飞书人员额度表同步、按人充值余额拦截。
- 如果自建 Dify / 网关返回 usage 信息，则记录精确 token；否则退化为按文本长度估算。
- Windows 版本支持远程发布与在线更新：客户端会读取 `latest.json`，下载新版安装器并在退出后启动安装。

## 说明

当前版本右侧是真实 WebEngine 浏览器。扫描器会在当前 BOSS 页面内执行 DOM 读取：先点击候选人列表项，再尝试打开“在线简历”，最后读取页面可见文本并做本地评分。

如果未登录、停留在验证码页、页面没有候选人列表，或 BOSS 页面结构变化导致选择器识别失败，本应用会记录失败并跳过，不会生成测试候选人写入候选人池。

## 调试与登录态

- 浏览器登录态保存在 `web_profile/`，重启应用后会继续使用同一套 BOSS 登录状态。
- 扫描日志保存在 `logs/scan.log`，工具栏里也有“打开采集日志”按钮。
- 日志会记录候选人列表识别结果、实际点击的人选文本、在线简历按钮识别方式、详情文本长度和预览。
- 飞书登录态保存在本地 SQLite 的 `app_settings` 中；运行配置保存在 `app_config.json`；AI 用量明细保存在 `ai_usage_logs`。

## Windows 在线更新

应用会读取 `windows_update_manifest_url` 指向的远程 JSON 清单。清单里需要至少提供：

- `version`
- `windows.installer_url`
- `windows.sha256`
- `windows.size`

客户端逻辑：

1. 启动后或点击工具栏 `检查更新`
2. 拉取远程 `latest.json`
3. 比较版本号
4. 下载新版安装器到用户目录
5. 校验 `sha256`
6. 退出当前程序并启动安装器

## Windows 发布流程

1. 修改 [app_version.txt](/Users/weiyifeng/ai-boss/source_share_clean_20260506/app_version.txt)
2. 在 Windows 上运行 `build_windows.bat`
3. 生成更新清单：

```bash
python scripts/generate_windows_update_manifest.py \
  --base-url https://your-domain/releases/1.0.0 \
  --installer release/AIBossWorkbench-Windows-Installer-v1.0.0.exe \
  --out release/latest.json
```

4. 把安装器和 `latest.json` 上传到公网可访问地址
5. 在客户端配置 `windows_update_manifest_url`

推荐的发布位置：

- GitHub Releases
- 阿里云 OSS + 自定义域名/CDN

## GitHub 自动发布（已配置）

仓库已包含工作流：

- [windows-release.yml](/Users/weiyifeng/ai-boss/source_share_clean_20260506/.github/workflows/windows-release.yml)

发布前请在仓库 `Settings -> Secrets and variables -> Actions` 配置：

- `APP_CONFIG_JSON`：完整的 `app_config.json` 内容（包含飞书 App ID / App Secret 等）

触发规则：

1. 修改 `app_version.txt` 为新版本（例如 `1.0.1`）
2. 提交并推送代码
3. 打标签并推送标签（必须与版本号一致）

```bash
git tag v1.0.1
git push origin v1.0.1
```

工作流会自动：

1. 在 Windows runner 打包
2. 生成安装器 `release/AIBossWorkbench-Windows-Installer-v版本号.exe`
3. 生成 `release/latest.json`
4. 发布到 GitHub Releases（同一个 tag 的 release 资产）

客户端建议固定使用这个更新清单地址（始终指向最新 release）：

- `https://github.com/MilkTeaCoder/ai-boss-workbench/releases/latest/download/latest.json`

## 飞书与 Token 用量配置

建议在左侧 `飞书与用量` 页面完成以下配置：

- 飞书登录：`App ID`、`App Secret`、授权地址、用户 Token 地址、用户信息地址、回调地址、Scope。
- 飞书云文档：推荐使用飞书 `多维表格` 作为“人员额度表”，只需要 3 列：`人员`（飞书人员选择框）、`充值token`、`已使用情况`。
- 登录限制：只有已经登记在人员额度表中的飞书账号，才能登录成功。
- 余额限制：应用每次调用 AI 后都会累计写回 `已使用情况`，当 `已使用情况 >= 充值token` 时，会提示“token已经消耗完，请联系管理员进行充值”。
- Dify / 网关：`llm_bridge_url`、`llm_bridge_timeout_seconds`、`dify_api_key`、`llm_bridge_auth_header`、`llm_bridge_auth_token`、`dify_user_id`。
- 字段映射：默认按这 3 列读取和写回多维表格：
  - `人员`
  - `充值token`
  - `已使用情况`
  - `最近一次消耗Tokens`
  - `最后使用时间`
  - `最后同步时间`
  - `应用`

## Dify 返回要求

如果你当前接的是自建 Dify 网关，想做“精确 token 统计”，需要网关把 Dify 响应里的 usage 一并透传回来。应用会优先解析：

- `metadata.usage.prompt_tokens`
- `metadata.usage.completion_tokens`
- `metadata.usage.total_tokens`
- `metadata.usage.total_price`
- `metadata.usage.currency`

如果没有这些字段，应用仍可运行，但限流与统计会退化为估算值。

下一步需要在真实招聘账号登录后，根据日志继续加固选择器、在线简历弹层读取、附件简历保存和受控打招呼动作。
