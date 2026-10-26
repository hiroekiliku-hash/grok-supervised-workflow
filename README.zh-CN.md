# Grok Supervised Workflow

让 Codex 通过官方 Grok CLI 派发小范围任务，同时由 Codex 负责独立审查、测试、恢复和整合。

**收到模型输出，不等于代码交付在推进。** 本 Skill 分开观察模型活动、工具调用和实际文件字节变化，避免 worker 连续思考却长期没有产物时一直等待。

[English](README.md) · [Skill](SKILL.md) · [设计参考](references/decisions.md) · [MIT 许可证](LICENSE)

## 安装

需要 macOS/Linux、Python 3.9+，以及已通过正常流程登录的[官方 Grok CLI](https://docs.x.ai/build/cli/reference)。运行时和测试仅使用 Python 标准库。

```sh
mkdir -p ~/.agents/skills
git clone https://github.com/hiroekiliku-hash/grok-supervised-workflow.git ~/.agents/skills/grok-supervised-workflow
```

目录已存在时先检查、备份自己的版本。仓库自带运行代码，不依赖作者电脑上的任何固定路径。[新版 Codex 文档](https://learn.chatgpt.com/docs/build-skills)使用 `~/.agents/skills`；较早安装可能仍使用 `~/.codex/skills`，请采用你的版本能识别的目录。可显式使用 `$grok-supervised-workflow`，或由 Codex 自动选择；未出现时重启 Codex。安装不会自行改写全局 AGENTS 指令。

本项目沿用官方 CLI 的已有登录，不创建 API key、不充值，也不修改全局 Grok 权限。账号资格、额度和实际计费由服务商决定。开发时验证过 Grok CLI 1.0.67 的原生流协议；未来协议变化可能需要适配。项目是社区实现，与 OpenAI、xAI 无官方隶属关系。

## 使用

先准备一次派发，不调用模型：

```sh
python3 scripts/dispatch.py examples/task.md --source . --expect solution.py --dry-run
```

移除 `--dry-run` 才是真实调用，会消耗服务商用量。对现有文件：

```sh
python3 scripts/dispatch.py /path/to/task.md \
  --source /path/to/project \
  --context src/module.py \
  --expect src/module.py
```

`--expect` 是必须改变的交付文件，不能把只作参考的上下文都列进去。新文件任务省略 `--context`。只读意见使用 `--profile review`，不传 `--expect`。

| 配置 | 总体 / 单次 | 首次响应 / 模型空闲 | 首产物 / 产物空闲 | 强度 / 尝试 |
|---|---|---|---|---|
| focused（默认） | 360 / 180 秒 | 120 / 90 秒 | 90 / 90 秒 | low / 1 |
| standard | 900 / 600 秒 | 180 / 180 秒 | 180 / 180 秒 | medium / 2 |
| review | 300 / 240 秒 | 120 / 120 秒 | 不要求产物 | low / 1 |

配置可覆盖，详见 `python3 scripts/dispatch.py --help`。先把大需求拆成一个可独立验收的行为；持续思考但缺产物时，缩小一次或由 Codex 接手，不原样无限重试。

## 监督与验收

- 模型思考、读文件、声称 Write、touch 或原样重写，都不能延长产物时限。
- 记录明确目标、不可变输入基线、源哈希、带观察时间的净化事件索引。重试不继承失败半成品。
- 检查越界输出、隐藏路径和符号链接；隐藏文件只检查元数据，不读取内容。整合前检查源漂移，避免覆盖并发改动。
- 正常结束与退出码零仅代表进入 `needs_review`，不代表业务正确。Codex 仍负责全部 diff、构建、行为和代码质量检查。
- 恢复耗尽时返回 `fallback_required`，由当前 Codex 决定拆分、修复或直接实现；脚本不会自动启动另一个 Codex。

运行记录保存在忽略的 `scripts/runtime/work/` 中，可能含项目代码与原始模型输出，分享前应检查。隔离工作目录不等于对恶意 worker 的完整安全边界；只传入授权且不含秘密的上下文。云端和原生 Windows 未验证。

## 离线测试

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

测试使用假 CLI 与本地临时文件，不调用真实模型、不需要账号。2026-10-26 另用便携包完成了真实登录派发：示例函数约 12 秒交付，通过 18,084 个独立整数输入检查。开发中也做过真实隔离派发与独立验收；这些结果不承诺所有任务都可靠。原始私有代码、日志和账户信息没有发布。
