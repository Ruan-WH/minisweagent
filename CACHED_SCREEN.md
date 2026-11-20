# 缓存样本筛选：先筛选，再对候选追问

本脚本不使用原版 `mini` 交互入口，复用此前实验的 mini 2.4.6 原生工具适配器。
正式实验由用户启动；`prepare` 不调用模型。

## Full 条件补测

用户随后授权正式运行第一批相同 5 个缓存样本的 full：12907、13033、13236、13398、13579。
输出目录为 `evaluation/evaluation_outputs/mini_swe/deepseek-cached-five-full-20261120-v1`。
`prepare-full` 创建单条件计划，`run` 只跑 5 次 full 并独立官方评分，不筛选追问候选。

此处 full 严格复用仓库 `evaluation.benchmarks.swe_bench.mini_swe.run.build_task(row, 'full')`：
表格原始 `original_issue` + `Later discussion / hints:` + `hints_text`，逐字核验输入。
它包含原有讨论中的信息与建议，与此前“不含 hints 的 ideal”不同；不应把二者合并统计。
没有初始问答、checker handoff、proxy 或追问。缓存基线、依赖、DeepSeek 配置、native v3、
2 worker、100 次调用/工具预算与隔离策略不变。官方评分发生在修复结束后，不回传模型。

新增完整原生回复结束标记支持仅影响执行结束判断，不代表官方 resolved。
若要重复该实验，先用新目录运行 `prepare-full --output <新目录>`，再 `run --output <新目录>`。
不能覆盖本次输出。

## 当前入口：第二批中断后的恢复

第二批 v4 已中断，原始结果全部保留。原因是 14365 partial 第 9 次回复返回 HTTP 200、
`finish_reason=stop` 和正常完成总结，但无工具调用/完成标记；旧适配器将其判为 FormatError，
触发整批停止，并连带中止了正在运行的 14182 ideal。随后后一个 worker 覆盖了 STOP.json，
原始原因需从实际 HTTP/轨迹还原。审计见旧目录的 `incident_audit.json`。

修复后的新批次启用 native v3：非空文本、`finish_reason=stop` 且无 tool/function call，
可自然结束为 **FinalResponse**。这不伪造完成标记、不执行新工具，也不将模型声称的测试通过
当作官方成功。空回复、截断回复、无效工具调用仍按故障停止；原生 bash、模型、采样、
系统提示词、实际任务输入与预算不变。新行为为实验入口显式启用，其他入口默认不变。
`STOP.json` 现在以独占创建方式保留首个原因，后续停止写入 `stop_events/`。

已准备恢复目录，用户正式启动：

```bash
cd /home/rwh/Ambig-SWE-Origin/Ambig-SWE-mini-export
bash run_cached_screen.sh run \
  --output evaluation/evaluation_outputs/mini_swe/cached-remaining-screen-20261120-recovery-v1
```

恢复复用 13453 partial/ideal、13977 partial/ideal、14182 partial，共 5 组已正常完成结果，
逐文件哈希核验并复制，不重新调用模型。其余 11 组从全新基线运行：
14182 ideal、14365 partial 是中断后的独立重跑，另外 9 组尚未运行。
不会继续旧消息、不提供旧补丁或失败分析，保留旧中断组，不追溯修改原来的失败状态。
旧 14365 partial 已有补丁但因协议中断，不会默默重分类为成功。

新目录的 `recovery_manifest.json` 标明复用来源、旧协议版本和新运行列表；
`summary.json` 保留逐组来源和协议版本。这是跨协议修订的恢复批次，不能描述为完全未改配置的重复实验。
仍先独立评分所有 16 组，再按相同条件挑选候选做新追问实验。2 worker，不自动追加多次尝试。
恢复准备阶段的新增模型调用为 0；目录内已有 HTTP 日志来自上述 5 组历史复制。

如需再次创建独立恢复目录，使用 `prepare-recovery --previous-output <旧实验目录> --output <新目录>`，
不要删除 STOP.json 或启动标记来强行续跑，也不要直接重跑旧 v4 目录。

## 第二批原始入口（已中断，保留作审计）

第一批 5 个样本的 10 次修复已经全部完成，12907、13579 两组均 resolved，另外三例两组均未修复，
没有进入追问组。第一批的输出与冻结源码完整保留，下面是独立的第二批实验。

第二批自动从本地缓存中排除第一批 5 个，以及已反复测试的 10554，纳入其余 **8 个**：
Astropy 13453、13977、14182、14365、8707、8872；Django 10097、10880。
仍为 2 个 worker、每样本 partial/ideal 各一次，共 **16 次筛选修复**；
仅对 partial 官方未修复、ideal 官方修复且无运行故障的候选启动全新的追问实验。
DeepSeek、采样、工具预算、输入定义、proxy/checker 和候选条件保持上一批设置。

由用户正式启动：

```bash
cd /home/rwh/Ambig-SWE-Origin/Ambig-SWE-mini-export
bash run_cached_screen.sh run \
  --output evaluation/evaluation_outputs/mini_swe/cached-remaining-screen-20261120-v4/experiment
```

离线准备的证据保存在第二批父目录 `remaining_preparation.json`，
8 个隔离检查、实际输入和配置保存在其 `experiment/` 子目录。
准备过程没有模型调用，也未执行官方隐藏测试。
准备过程中失败的 v1/v2/v3 输出保留，最终使用 v4，禁止覆盖旧结果。

本批有三项环境适配，均不提供修复信息：

- 给每个缓存镜像建立独立的 `mini-screen-cache/...` 镜像名，保留原标签。
  直接在 network=none 容器中准备并保存，不拉取镜像、不安装新依赖。
  8872 的基线检查失败源于子模块文件权限变化；8707/8872 的 `astropy_helpers` 均恢复为基线 gitlink
  指向的精确提交，并移除子模块 Git 入口。正式容器随后清理整个父仓库历史，保留仅基线代码。
- 官方 scorer 只覆盖 TestSpec 的镜像名称，指向同一冻结镜像；断言 eval.sh 与原 TestSpec 完全一致。
  补丁应用、隐藏测试、测试命令、解析器与 resolved 评分仍使用原官方实现。
- Django 依赖从官方评测记录的 `environment_setup_commit` 读取精确缓存 Git blobs，并校验 SHA256。
  仅替代生成规格时的依赖 HTTP GET，不修改依赖内容或评测规则。表格中缺失的 setup commit
  不覆盖官方记录。Excel 将版本 `5.0` 存成 `5` 时，仅规范化版本元数据为官方键 `5.0`。

如需为新的输出目录重新准备剩余缓存，可运行以下命令；这一步不会调用模型：

```bash
bash run_cached_screen.sh prepare-remaining \
  --previous-output evaluation/evaluation_outputs/mini_swe/cached-five-screen-20261120-v3 \
  --output /绝对路径/新的准备目录
```

完成后用 `run --output /绝对路径/新的准备目录/experiment` 正式启动。
已有镜像准备可以用 `--reuse-preparation` 指向第二批 v2 父目录复用，镜像 ID 会重新验证；
它只复用准备镜像，不复用模型轨迹或修复补丁。

## 本次选择与并发

本地找到 14 个缓存 issue。选择执行镜像、官方评分镜像及其依赖镜像均已缓存的 5 个：

| 样本 | 任务 |
| --- | --- |
| astropy__astropy-12907 | 嵌套模型的 separability matrix |
| astropy__astropy-13033 | TimeSeries 必需列错误信息 |
| astropy__astropy-13236 | 结构化数组加入 Table 的转换行为 |
| astropy__astropy-13398 | ITRS 与观测坐标系转换 |
| astropy__astropy-13579 | 切片 WCS 的 world_to_pixel |

8872 曾进入首次离线准备，但基线重置后无法通过工作树无修改检查，未调用模型；失败记录保留于
`evaluation/evaluation_outputs/mini_swe/cached-five-screen-20261120-preflight-v1/`。
13398 替换了它。全部是 Astropy，属于缓存驱动的小规模筛选，不能代表跨项目总体效果。

电脑为 i9-14900HX、32 逻辑 CPU、约 16GB RAM。准备时可用内存约 6.3GB、swap 已用约 4.2GB，
因此固定 **2 个模型执行 worker**，评分顺序执行。Docker 每容器上限 RAM 6GiB、含 swap 8GiB，
这是上限而非预留；两容器仍共享宿主内存，实际发生 OOM 时计入运行故障，不当作模型修复失败。

## 输入条件：与 10554 的区别

这些样本没有 10554 那样经保存核验的 ideal/partial 固定回答。本轮使用新筛选 fixture：

- 公共任务：Ambig-SWE 表格中已有的 `problem_statement` 摘要。
- 固定初始问题：询问问题、已有复现细节与预期行为，所有组完全相同。
- partial 不追问：初始回答为已有摘要，逐字复用。
- ideal 不追问：初始回答为原始完整 issue，不包含讨论 hints。这里 ideal 只是本轮完整 issue 条件的简称，
  不保证信息充分，也不等同于 10554 的历史 ideal QA。
- 优先使用本地官方评测 dataset 保存的原始 issue 文本；否则只解码 Excel `_x000D_` 换行转义。
  保存原始表格字段、实际输入、来源及 SHA256。
- 不生成新的初始 QA，不向模型提供文件列表、补丁、隐藏测试或过去结果。
- 三组均用同一 handoff 函数直接附加公开 QA，没有另一个模型总结或重写信息。

## 执行流程

1. 5 个样本各跑 partial 和 ideal，共 10 次全新修复，最多同时运行 2 次。
2. 所有筛选修复结束后，用独立容器中的官方 SWE-bench **2.0.13** `run_instance` 评分；
   复用缓存镜像，不修改评测脚本、测试、解析器或评分规则。空补丁也执行此单样本入口，
   不用批处理入口的空补丁跳过逻辑。
3. 仅选取无执行故障、补丁成功应用且 partial `resolved=false`、ideal `resolved=true` 的样本。
4. 对每个候选在全新基线容器中跑一次 partial＋追问，随后独立评分。没有候选就结束。
   不重复补跑、不继续旧轨迹、不根据隐藏测试反馈修改代码。

这是前两步试验准备，不执行每组补跑到 3 次的第三步。
此前 12907 的成功记录已知，因此这个样本不是盲选；本轮仍完整保存两组结果，不据此宣称总体收益。

## 模型、追问与预算

- 所有角色：现有 `config.toml` 中的 `llm.hidden_deepseek_flash`，模型 `openai/deepseek-flash`，
  API `https://api.deepseek.com/v1`；实际返回模型名另行记录。API key 不写入 manifest 或 HTTP 日志。
- 修复：temperature=0、top_p=1、max_tokens=16384、timeout=180 秒；不覆盖思考设置，保留 reasoning_content。
  native bash tools、tool_choice=auto、parallel_tool_calls=false，多条工具顺序执行。
- 修复至多 100 次 agent query、100 次实际 HTTP 请求（含 SDK 重试）、100 次实际工具执行；
  单命令 120 秒、总修复 7200 秒。模型调用、HTTP 重试、工具调用、token 与时间分别统计。
- SDK 保持此前最多 2 次瞬时错误重试，整次任务不自动重试。账户、额度错误停止；批次停止信号也阻止后续 HTTP 重试。
- partial/ideal 无 proxy、无 checker、无追问。
- 候选追问：沿用 `simple-focused-answer-v1` 的独立 pre-repair checker，最多 2 次追问。
  **不是修复 agent 在执行中自主提问**；本轮证据应准确归因于此澄清流水线。
- proxy 使用 `source-grounded-reviewed-v3`：DeepSeek 自由生成自然回答，再由 DeepSeek 校验来源；
  每次回答至多 2 个候选，每个有效候选做 1 次审查；仅自然回答进入修复输入。
  无规则表、无 fact ID、无官方评测反馈。
- proxy 只知道原始 issue，`hints` 为空，使信息来源与本轮 ideal 一致；不会额外提供修复讨论。
  采样 temperature=0、top_p=1、JSON，timeout=120 秒、SDK 不重试、不显式设置 max_tokens。
  保留原有长度、引用、代码逐字匹配及因果解释限制；模型审查不保证杜绝所有幻觉。
- checker、proxy、review 的请求/回复、token 与耗时通过澄清日志及整体时间保存；修复单独计时。

## 隔离与故障

`prepare` 在一次性容器中验证工作目录、Python 实际导入路径和写入可见性；保留依赖清单。
复用 `reference_history.py`，仅清理容器的 Git，核对 refs、reflog、残留对象、远程访问与 network=none。
不挂载宿主目录、Docker socket 或评测数据，移除缓存的 setup 脚本与评测 metadata。
正式修复前重新检查，同一镜像 ID、依赖与源码哈希必须一致。

第 20 次工具执行后自动检查原生请求/响应、格式残片、导入写入一致性和失败管道退出码，
保存 `checkpoint20.json`。这不能替代对所有模型自写测试的语义审计；实际完整命令及输出均保留。
不会因为暂时没有生产修改而停止。协议、澄清、账户、容器等故障会留下 `STOP.json`，
停止启动新任务，其他活跃 worker 在当前请求/工具结束后停止后续请求。
`Ctrl+C` 同样设置停止信号，并给活跃 worker 有限时间保存轨迹；未强行杀死已在途的 API 请求。

## 第一批历史入口

第一批已完成，以下为历史命令，不能在原目录重复运行：

```bash
cd /home/rwh/Ambig-SWE-Origin/Ambig-SWE-mini-export
bash run_cached_screen.sh run \
  --output evaluation/evaluation_outputs/mini_swe/cached-five-screen-20261120-v3
```

不需要进入虚拟环境。启动器使用已安装 Python，且取消本进程的 HTTP/SOCKS 代理环境变量。
`run` 才会调用模型、执行评分和候选追问。不要给该命令加原版 `mini` 的 `-y`。

查看进度：输出目录的 `console/*.log`，以及各 `runs/<issue>/<condition>/tool_events.json`。
最终查看 `summary.tsv`、`summary.json`、`candidates.json` 和 `completed.json`。
每组保存实际 task、完整轨迹、HTTP 请求/回复、工具事件、首次生产修改、原始 model.patch 和独立评分报告。
评测器的 git apply/git show 等操作只在评分容器中进行，输出不返回模型。

输出目录不会覆盖或自动续跑。若需要修改脚本/配置或重试，请用一个新的目录先做离线准备：

```bash
bash run_cached_screen.sh prepare --output /绝对路径/新的实验目录
bash run_cached_screen.sh run --output /绝对路径/新的实验目录
```

代码为本地新增文件，本次没有提交或 push。

## 13236：issue+hints 需求澄清

新增可选 `--proxy-source issue-hints`，默认仍为旧的 `issue` 策略；不改既有冻结实验。
新策略 `requirements-issue-hints-v4` 使用原始 issue 与表格 hints，允许披露外部行为和后续明确的需求共识，
禁止披露源码文件/位置、代码块/补丁、实现步骤、删改代码/加测试建议、根因与内部机制，原文有这些内容也不能转述。
它是通用约束，没有针对13236的固定回答。保留精确引文审计、独立审核、最多一次纠正；私有引文不进入修复输入。
checker、proxy生成/审核、修复均为原 DeepSeek 配置，初始 partial 输入不变。

```bash
bash run_cached_screen.sh prepare-followup --proxy-source issue-hints \
  --instance-ids astropy__astropy-13236 --output evaluation/evaluation_outputs/mini_swe/NEW_UNIQUE_DIRECTORY
bash run_cached_screen.sh run --output evaluation/evaluation_outputs/mini_swe/NEW_UNIQUE_DIRECTORY
```

结果与完整限制见 `experiment_records/README.md`；Git 归档只包含紧凑记录，完整轨迹在本地输出目录。
