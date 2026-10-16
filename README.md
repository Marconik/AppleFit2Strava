# Apple 健康 → Strava

本地免费转换工具。默认读取本项目 `apple_health_export/导出.xml`，路线读取同目录的 `workout-routes/`；不使用 `export_cda.xml`。原始文件保持不变。

发布副本不包含任何真实健康数据、轨迹、账号、上传记录或浏览器观测。数据目录及原有子目录通过 `.gitkeep` 保留为空；本机 Conda 环境、缓存和历史备份内容不发布。

## 首次使用

```sh
conda env create --prefix ./.conda --file environment.yml
conda activate ./.conda
```

推荐下载 GitHub Release 中的 `AppleFit2Strava-版本-agent-install.zip`，把压缩包交给支持本地执行和技能安装的 agent，说“请安装这个 skill”。安装时无需账号或健康数据；首次同步时再配置。

直接使用源码时，用自己的 Strava athlete ID 和起始日期创建项目配置：

```sh
python setup_project.py --project . --athlete-id 你的数字ID --start YYYY-MM-DD --sport all --location all
```

`sync_config.example.json` 仅说明格式，不是有效账号配置；程序创建的 `sync_config.json` 已被 Git 忽略。已初始化项目不重复运行 setup_project。

将解压后的健康导出放入 `apple_health_export/`，路线放入 `apple_health_export/workout-routes/`。不要提交个人配置或这些目录中的数据；仓库已忽略这些本机文件。

完整 Skill 位于 `skills/apple-health-strava-sync/`。将该目录安装到宿主的技能目录，或让宿主加载其中的 `SKILL.md`，并明确指定项目路径。宿主需提供本地 Python、已登录浏览器操作、本地文件上传和用户确认能力。

独立本地转换命令默认筛选户外骑行，日期范围为 2000-01-01 至 2099-12-31；Skill 使用你填写的配置及本次指令。

## 通用 Skill 上传

本仓库附带 `apple-health-strava-sync`。宿主支持 Skill 发现时按其语法调用，否则显式加载 SKILL.md。覆盖 `apple_health_export/` 后，向支持此 Skill 的宿主说：

> 使用 $apple-health-strava-sync，把新增户外骑行上传到 Strava。

技能先通过已登录浏览器 检查全部待上传记录，展示全部疑似重合项及云端链接供你确认，然后上传未排除项。你确认排除的记录记为 `exist`，实际上传成功记为 `uploaded`，二者以后都会自动跳过；无需手工标记每批。默认范围由你填写的 `sync_config.json` 管理；结束日期为空时使用导出数据的最新日期，调用时可指定其他范围。

`sync_strava.py prepare --preview` 只读预览；`prepare` 准备文件；`queue --manifest …` 预览待处理范围；`receipts` 查看网页回执。这些本地命令本身不操作浏览器，实际上传由 Skill 使用宿主提供的浏览器能力完成。

### 多运动类型和无距离记录

支持中英文名称、Apple 类型、Strava 类型或 all；用 .conda/bin/python sync_strava.py sports 查看映射。例如：

> 使用 $apple-health-strava-sync，同步 2026-01-01 至 2026-10-15 的瑜伽，先检查全部疑似项和地点，等我确认后再上传。

~~~sh
.conda/bin/python sync_strava.py prepare --sport Yoga --location all --start 2026-01-01 --end 2026-10-15
~~~

支持跑步、步行、徒步、游泳、力量、HIIT、瑜伽、普拉提、椭圆机、划船、球类和滑雪等。无直接对应的 Apple 类型需确认 Workout 或报告建议的具体类别。切换到其他运动且未指定 location 时默认包含室内外。

无距离运动按开始时间、同口径时长及大致地点排查。地点来自真实 GPX、云端可见信息或用户提供的本次场所；未知和冲突均明确标注，仍保留疑似项。无距离项必须填写地点排查证据才能创建审核。用户补充可用 set-place --manifest … --file … --label … --evidence … 保存到 state/activity_places.json，不生成轨迹；修改地点后须重新审核。

清单包含双方时长、地点证据和上传类型。非骑行/跑步通过 TCX Other 上传，再设置并核对确认的类别。无距离时 TCX 必填 0 是占位值，manifest 仍保留缺失。所有重合项仍等用户确认才标为 exist。

### 检查云端是否已存在

向支持此 Skill 的宿主说：**使用 $apple-health-strava-sync，检查云端活动与本地待上传记录是否重合，只检查不上传。**

技能通过已登录浏览器 读取自己的活动列表和必要详情，再执行本地 `cloud-check`。同一分钟、同类型、距离差不超过 100 米或 5%（取较大值）的唯一对应项标为 `strong_overlap`；缺少开始时刻、多个候选或数值冲突标为 `needs_review`。未读完整的日期范围只报告“未观察到”，不能判断云端不存在。

```sh
.conda/bin/python sync_strava.py cloud-check --manifest exports/某次导出/manifest.json --observations state/cloud_checks/某次观测.json --output state/cloud_checks/某次报告.json
```

生成内部 JSON 报告，在对话中列出检查结果及活动链接，不生成查重 CSV，不上传、不修改本地上传标记。网页观测文件由技能采集，不能用本地清单代填；每次检查应重新采集。Strava 的移动时间与 Apple 有效时长计算不同，不据此直接判重。检查结果是匹配证据，不会自动改状态。开始上传必须完成以下审核：

1. `review --manifest … --observations …`：检查覆盖全部待上传日期，生成内部 JSON 审核报告和决定模板。
2. 在对话中按编号列出所有疑似项及每个候选的本地/云端时间、距离、时长、地点、活动链接及原因，不生成表格附件，等待用户决定；无疑似项也确认后开始。
3. `confirm-review --manifest … --review-id … --decisions … --confirmation "用户确认原文"`：模板中每个疑似文件及需确认类型的文件选择 `exist` 或 `upload`；exist 附对应云端链接；需确认类型的上传条目填写 strava_sport。一次性登记全部排除项。
4. `upload-queue --manifest … --review-id …`：只提供已确认、未排除且未成功上传的文件路径。

上述均为 sync_strava.py 子命令。审核有效期为观测后的 24 小时；清单变化、覆盖不足或漏掉疑似项决定时不能上传。审核快照和确认原文存于数据库 `upload_reviews`，报告存于 `state/upload_reviews/`。停止后下次续传重新审核。

## 日常使用

双击 `启动.command`：

1. **预览/导出**：输入路径时直接回车即可使用项目内数据。uploaded/exist 训练自动跳过，每次生成独立 TCX，按 15 条分批。
2. **登记已上传**：在 Strava 网页上传成功后，选择已成功的单个 TCX 或整个 batch 文件夹。整批有失败时，只登记成功条目。
3. **查看上传记录**：显示 uploaded、exist 和待上传列表。
4. **撤销上传标记**：误登记或在 Strava 删除后需要补传时使用；不会删除 Strava 上的任何活动。

上传入口：https://www.strava.com/upload/select 。使用技能时先完成全部查重及用户确认，再开始上传。

**生成文件不代表上传成功。手动上传使用菜单 2 登记；技能上传会读取网页成功回执并自动登记。**

## 上传列表

- `state/upload_history.sqlite3`：持久记录库，同时保存导出、确认、撤销操作历史。
- `state/upload_history.csv`：自动更新的可读列表。直接编辑 CSV 不会改变程序状态。
- `exports/…/manifest.json`：本次导出清单；`activities.csv` 包含数据缺失提示和导出当时的状态。

记录标识由“运动类型＋UTC 开始时间＋UTC 结束时间”组成，不依赖文件名、排列顺序或来源名称。以后替换 `apple_health_export/` 为新导出数据，仍会跳过同一训练。请保留 `state/` 和导出清单。

训练结束时间被修改后会视为新记录，请人工核对。其他方式上传的活动可用上面的云端检查发现；仅运行本地 queue 不会访问云端。

## 命令行

在项目目录运行：

```sh
# 预览；无需提供输入路径
.conda/bin/python health_to_strava.py --preview

# 导出到新的自动命名文件夹
.conda/bin/python health_to_strava.py --export

# 整批确实上传成功后登记（替换为实际目录）
.conda/bin/python health_to_strava.py --mark-uploaded exports/某次导出/batch_001

# 只登记部分成功的文件
.conda/bin/python health_to_strava.py --mark-uploaded exports/某次导出 --files 0001_某日期_Cycling.tcx

# 查看记录或撤销登记
.conda/bin/python health_to_strava.py --history
.conda/bin/python health_to_strava.py --unmark-uploaded exports/某次导出/batch_001

# 修改日期；其他筛选选项见 --help
.conda/bin/python health_to_strava.py --export --start 2026-10-11 --end 2026-10-15
```

可选：`--sport cycling`、`--location outdoor/indoor/all`、`--source 来源名称`、`--inventory`、`--include-unknown`、`--output 新目录`。`--include-uploaded` 可临时导出 uploaded/exist 训练，原有标记不变。`--history-file 路径` 可使用单独记录库。

## 数据与验证

- 日期默认按记录原始时区、训练开始日期判断；可用 `--timezone Asia/Shanghai` 指定固定时区。
- 路线、心率、骑行功率、踏频和速度按原数据导出；心率缺失不会补造。区间汇总样本无法恢复高频曲线。
- 优先采用训练同名传感器来源；其他来源和缺失数据在 warnings 中提示。
- 总距离/有效时长优先采用 Apple 汇总；轨迹累计距离由 GPS 计算。Strava 可能重新计算数值；暂停事件未单独编码。
- 骑行为 Biking、跑步为 Running；其他运动使用 TCX Other，上传时需核对类型。
- XML 流式读取，无第三方 Python 依赖。运行 `.conda/bin/python -m unittest -v` 验证程序。

## 迁移到其他宿主

复制完整 Skill 文件夹（含 scripts 和 references），通过宿主自己的机制加载 SKILL.md；不要求特定浏览器或工具名。Skill 自带 Python 程序，调用时显式指定 --project。仅复制 SKILL.md 不足以携带全部功能。可选 agents/openai.yaml 仅是界面元数据，其他宿主可忽略。

保留并迁移项目 sync_config.json、state/ 和所需 exports/；同一账号的清单按运动类型与 UTC 起止时间识别记录。更换项目绝对路径后重新 prepare、云端检查及确认，不复用旧路径绑定的审核。不同账号使用不同项目；新用户通过 Skill 内 setup_project.py 创建配置。详见 Skill 的 references/harness.md。Python 使用 3.10+，所需 IANA 时区数据库不可用时先安装 tzdata 或系统时区数据。

测试中的账号、活动链接、坐标和运动样本均为合成测试数据，不是个人活动记录。

## 构建安装包

提交改动并保持项目版与 Skill 内脚本一致后，运行 `python build_release.py`。构建仅使用当前 Git 提交，输出 `dist/AppleFit2Strava-版本-agent-install.zip`。包内 README.md 来自 packaging/README_AGENT.md，原源码说明保留为 README_USER.md；release.json 记录版本、源提交、文件清单和字节数。检查采用 ZIP CRC 和逐文件内容比对，不使用 SHA256。

发布时上传这个专用 ZIP；GitHub 自动生成的 Source code 压缩包是源码快照，不包含构建时生成的 agent 安装入口替换。
