# 实验记录归档

本目录保存 2026-11-20 mini-SWE-agent 缓存筛选、full 对照和 13236 追问实验的紧凑记录。
`20261120/index.json` 列出 12 个实验目录、34 个修复运行记录（含中断及恢复复制的记录，**不能直接视为34次独立实验**）。

包括：脱敏配置、版本与输入哈希、实际输入、公开追问/回答及审核结论、原始最终补丁、运行统计、官方评分和故障审计。
准备失败、协议中断和失败实验均保留。plan 中 host-only evaluation_record 的 gold patch/隐藏测试正文不上传。

原始 HTTP 请求/响应、完整模型轨迹、工具输出、冻结源码及评测日志约 1.5 GB，继续保留在本机
`evaluation/evaluation_outputs/mini_swe/`（该目录被 Git 忽略）。每个实验的
`local_artifact_inventory.json` 记录全部本地文件的路径、大小与 SHA-256，可核对完整记录。
**此 Git 归档不包含完整轨迹，不能替代完整原始实验备份。**

## 13236 的探索结果

| 条件 | 记录目录 | 官方 resolved | 目标 / 回归 |
| --- | --- | --- | --- |
| partial，不追问 | cached-five-screen-20261120-v3 | false | 见对应 report |
| full，issue+hints | deepseek-cached-five-full-20261120-v1 | true | 2/2，644/644 |
| partial，追问仅 issue | deepseek-13236-partial-followup-20261120-v1 | false | 0/2，644/644 |
| partial，追问 issue+hints | deepseek-13236-partial-followup-issue-hints-20261120-v2 | true | 2/2，644/644 |

issue+hints 追问补回后续讨论的最终需求：直接创建 Column，不等待最初建议的 FutureWarning 周期。
修复模型在第8次工具调用、约20秒开始修改，最终删除自动转为 NdarrayMixin 的分支。
这是单次探索对照；扩大信息源和采用通用需求披露策略同时发生，不证明稳定提升或唯一失败根因。

必须保留的限制：issue+hints v1 因新增审核提示缺少 JSON 声明而 HTTP400 中断，未执行修复；
v2 修正该声明后独立运行。v2 的第8次工具输出有 ImportError，但 shell 后续 git diff 掩盖退出码；
模型下一步自行修复。该现象在完成后审计发现，未在当时停止。其他本地测试也有失败，官方通过仅指官方指定目标/回归集合。
详见对应 `analysis.json`。官方评分在独立容器进行，结果未回传模型，没有评分后再修补。
