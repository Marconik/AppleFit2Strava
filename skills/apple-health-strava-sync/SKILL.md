---
name: apple-health-strava-sync
description: 通过宿主提供的本地 Python 和已登录浏览器，将 Apple 健康运动数据同步到 Strava；支持多种运动及无距离运动的时间、时长和地点排查，上传前完整查重并请用户确认，排除项记为 exist，成功项记为 uploaded。支持只读云端检查。适用于同步、补传或检查重复运动数据。
---

# Apple 健康同步到 Strava

## 宿主与项目

不依赖特定 harness、浏览器品牌、操作系统或工具名称。首次执行先读 [references/harness.md](references/harness.md)，确认本地执行、页面读取/交互、本地文件上传和用户确认能力；用宿主提供且允许的工具实现。仅编辑 Skill 或本地预览不需要浏览器。

本 Skill 自带 scripts/ 下的完整 Python 工具。项目与 Skill 可以分开放置；始终显式传 --project，避免把用户数据写到 Skill 安装目录。

- 项目位置按用户本次指定、APPLE_HEALTH_STRAVA_PROJECT 环境变量、当前工作目录/祖先目录中的 sync_config.json、可选 local.json 的 project 字段依次查找。候选冲突时按上述优先级，仍无法唯一确定时询问；不硬编码任何用户路径。
- 优先项目现有 Conda 环境，其次宿主指定 Python，再用可用的 Python 3.10+；验证 zoneinfo 能加载本次所需 IANA 时区。选择实际解释器路径，不要求特定环境名称。
- 现有项目读取 sync_config.json，保持账号、类型、日期与持久清单。browser 字段仅作偏好，auto/空值表示发现可用浏览器；不将旧品牌值当作硬性要求。用户本次指令优先。
- 新项目使用 scripts/setup_project.py，必须取得实际 athlete ID 和起始日期；具体命令见宿主参考。不把示例账号/日期当真实配置。
- 输入由 input_directory 指定，通常是项目内 apple_health_export/导出.xml 与 workout-routes/；使用已解压目录，原始数据不改动。保留 state/ 和 exports/；不要用 SHA256。
- 文中 SYNC 是逻辑命令，表示参数数组 [选定的 Python, Skill目录/scripts/sync_strava.py, "--project", 项目绝对路径]。使用宿主参数化执行接口；只有 shell 时按该 shell 正确引用路径。不要直接执行字符串 SYNC，也不要把示例省略号作为参数。

用户要求同步/上传时，必须先完成下节全部云端检查及用户确认，再开始传输；确认针对本次完整清单，不逐批重复询问。此前笼统的上传授权或可见性确认不能代替用户看过本次疑似清单后的决定。仅要求创建/编辑技能、查看或预览时不上传。只有待处理 TCX 传给 Strava，不传原始健康 XML。

## 准备

使用解析后的 SYNC 执行下列子命令（可先 `prepare --preview` 只读检查）：

```sh
SYNC prepare
# 指定类型与范围：
SYNC prepare --sport Yoga --location all --start YYYY-MM-DD --end YYYY-MM-DD
SYNC sports
SYNC queue --manifest /绝对路径/manifest.json
```

`prepare` 每次读取新 XML，跳过 uploaded/exist，只导出待处理训练并返回新 manifest。`queue` 仅作预览，返回完整待处理范围，不提供可上传路径；用户确认后由 `upload-queue` 返回每批最多 15 条的实际路径。pending 为 0 时结束，不打开上传流程。检查 warnings；缺失字段按原样说明，不能生成虚构曲线。

类型映射见项目 sport_types.py，sports 列出中英文别名和上传类型；支持跑步、步行、徒步、游泳、力量、HIIT、瑜伽、普拉提、椭圆机、划船、球类、滑雪等。--sport all 包含所有类型；显式改为另一类型时默认 location=all，避免漏掉室内运动。用户指定室内/户外时传对应 --location。

原始 Apple 类型保留在清单和记录标识中。strava_sport/strava_label 是上传目标；type_confirmation_required 表示通用或多义对应，需用户选择。例如 PaddleSports 不能擅自定为皮划艇，拳击等无直接类别时需确认 Workout。未知类型不能静默转换。依据 [Apple 类型说明](https://developer.apple.com/documentation/healthkit/hkworkoutactivitytype) 与 [Strava 类型列表](https://developers.strava.com/docs/reference/#api-models-SportType)。

## 云端重合检查

每次开始上传前，必须读取 [references/cloud-check.md](references/cloud-check.md)，完整检查本次全部待上传记录的云端日期范围，包括所有批次。先完成检查，再展示所有疑似项，不边检查边上传。

1. 采集当前账号真实网页数据后执行 `review --manifest … --observations …`。不完整的覆盖或超过 24 小时的观测不能进入上传审核。
2. 展示总待上传数、无候选数、全部疑似记录及其所有云端候选：本地日期/时间、类型、距离、有效/全程时长，云端时间/日期、距离、时长口径、大致地点及证据、活动链接、匹配原因。无距离记录必须执行参考文档中的地点排查；缺失或冲突也明确展示。直接在对话中按本次清单编号列出全部疑似项及每个候选，不生成或附加查重 CSV，也不另建表格附件。数量较多时分段输出完整编号列表，保持编号与本地文件对应；不能只给几个样例或仅给汇总数字。内部 JSON 报告和决定模板继续保留，用于审核及记录用户决定。
3. 请用户决定哪些疑似项排除、哪些继续上传，并确认上传其余无候选记录。可接受“全部排除”“除第 X 条外都排除”等无歧义批量决定；多候选项需要确定对应云端链接。未作决定的疑似项不能擅自排除或上传。没有疑似项也展示检查结果并确认开始。另列全部 type_confirmation_required 项及建议类型，请用户一并确认。地点查不到时如实标为未知，可请用户补充场所；不得自行猜测。
4. 将用户决定填入生成的 `-decisions.json`：每条疑似记录及需确认类型的记录必须有 `action: "exist"` 或 `"upload"`；exist 必须附报告内的实际 `activity_url`。需确认类型且决定上传的条目必须填写用户选定的 strava_sport，取报告建议值或 Workout；即使没有重合候选也不能省略类型确认。保持 review_id 和文件名一致。用户回复之前不填写推测决定，不执行 confirm-review。
5. 执行 `confirm-review --manifest … --review-id … --decisions … --confirmation '用户确认原文'`。它在一个事务里把排除项改为 exist 并保存确认记录；其余仍为 exported，只有真实成功回执才能改为 uploaded。
6. 执行 `upload-queue --manifest … --review-id …`，仅按该命令返回的 paths 上传。审核未确认、范围变化或观测过期时重新检查并确认，不能用旧批次目录绕过。

仅要求检查时使用 cloud-check，到报告结束，不创建上传授权、不更改活动状态。

## 浏览器上传

使用 [宿主能力约定](references/harness.md) 发现已登录 Strava 的会话和标签页；先读取宿主浏览器工具说明。不要固定浏览器、工具名、标签 ID、元素编号或文件选择器 API。

1. 复用用户的 Strava 标签页，从页面个人资料链接核对 athlete ID 与配置中的 `expected_athlete_id` 一致。账号不同或登录失效时停止传输，告知用户处理；不自动改配置来匹配其他账号。
2. 打开 https://www.strava.com/upload/select。工具本地每批最多 15 个、每个不超过 25 MB；若当前页面限制更低则缩小批次，不据此断言账号的总配额。
3. 用户确认后，通过宿主支持的文件上传控件、文件选择器或系统对话框，选择 upload-queue 返回的准确路径。选择文件可能立即开始传输，不能用于确认前的能力测试。
4. 接口失败时只采用宿主允许的替代方式，并重新观察页面。续传不能直接全选旧批次目录；只选队列中的待上传文件，必要时逐个上传。Python 与浏览器不共享文件系统时，按宿主文档传递确认后的 TCX，保持路径与原文件一一对应。
5. 等页面处理完成。根据本次 UI 核对日期、时间、运动类型和文件对应关系；如果有“保存/查看/保存全部”等最终保存步骤，继续完成。TCX 对骑行、跑步外的类型使用 Other；按审核队列的 strava_sport 在上传/编辑页选择正确类型并保存。若页面不能设置该类型，暂停该条并报告，不虚报成功。只调整本次新上传活动到用户确认的类型。沿用账号现有可见性设置，不擅自改变隐私、标题、装备或其他既有活动。
6. 每条有明确保存成功的页面结果及活动详情链接后，执行下节回执登记。不能把“已选择文件”“处理中”或“已解析但未保存”当作成功。

根据实时页面识别控件和结果，不假设未观察到的 DOM。具体浏览器实现可以是浏览器工具、宿主允许的自动化框架或桌面操作工具；遵守宿主限制，不提取 cookies/会话凭据，不用私有 HTTP 请求替代可见页面检查。

## 逐条登记、续传

确认网页中的文件名或时间/距离等信息能对应唯一训练后：

```sh
SYNC record \
  --manifest /绝对路径/manifest.json \
  --file batch_001/实际文件名.tcx \
  --athlete-id 实际核对的账号ID \
  --activity-url https://www.strava.com/activities/实际活动ID \
  --review-id 本次已确认的审核ID \
  --outcome saved \
  --sport-type 网页实际核对的类型键 \
  --evidence '实际页面中可见的成功结果与文件对应信息'
```

- 无距离训练通过开始时间、时长及地点等确认对应关系，不能用 0 距离或标题单独判断。其他运动需核对已保存类型与队列一致，record --sport-type 不匹配会拒绝登记。
- 证据和活动 URL 必须来自实际网页观察，不从本地文件推测或编造。回执与 uploaded 状态在同一数据库事务内保存，CSV 自动刷新。
- 上传中若页面新提示重复，停止后续传输；先登记已经明确保存成功的记录，再重新检查全部剩余记录并展示新增候选，请用户确认。不得自行用 duplicate 标为 uploaded，也不删除云端活动来重试。
- 部分成功只登记成功条目，立即刷新 `upload-queue --manifest … --review-id …`；失败和未知状态留在队列。没有可靠结果时先检查活动页面，不能盲目重复提交。明确的暂时错误最多重试一次；持续错误、限流或账号问题时停止当前上传，汇报剩余项。
- 每批完成后登记回执，再处理下一批；同次确认内连续分批无需重复确认；用户停止后另一次调用续传，须重新检查全部剩余记录并确认。重新 prepare 生成新 manifest 也需要新审核。不要用整批手动 mark-uploaded 代替逐条网页证据。
- `sync_strava.py receipts` 可查看保存的活动链接与证据。需要撤销标记时仅按用户要求调用 scripts/health_to_strava.py --history-file <项目/state/upload_history.sqlite3> --unmark-uploaded <实际清单>，这不会删除 Strava 活动。

最后报告新增成功、确认已存在、失败/待处理的数量及活动链接。遵循当前宿主的页面证据/截图要求；不得把本地测试通过写成真实上传成功。
