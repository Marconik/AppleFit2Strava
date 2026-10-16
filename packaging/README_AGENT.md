# AppleFit2Strava：Agent 安装入口

用户将本安装包交给你并说“请安装这个 skill”时，按下面流程完成安装。遵守当前宿主的权限和安装规则，不要求用户手工运行命令或编辑配置。

## 安装

1. 若收到 ZIP，先查看条目，拒绝绝对路径、越界路径和符号链接，再解压到独立工作目录；已解压则直接使用。根目录包含本 README、`install_skill.py`、`VERSION` 和 `skills/apple-health-strava-sync/SKILL.md`。不要把外层项目目录误装成 Skill。
2. 读取宿主已有的 Skill 安装约定，确定技能发现目录和命名规则。安装目录应为该目录下的 `apple-health-strava-sync`。不要根据浏览器、操作系统或包名猜测宿主，也不要固定使用某个产品的目录。宿主有专用安装接口时优先遵循其约定，确保包含全部 `scripts/`、`references/` 和 `VERSION`；`agents/openai.yaml` 是可忽略的界面元数据。
3. 选择实际可用的 Python 3.10+，优先宿主/项目已有 Conda 环境。缺少解释器时按宿主规则配置环境；已有可用 Python 时无需安装 Conda。Windows 或精简环境缺少 IANA 时区数据库时，在选定环境安装 `tzdata`；`environment.yml` 提供可选环境定义。
4. 通常使用参数数组 `[Python绝对路径, 安装包根目录/install_skill.py, "--dest", 技能最终绝对路径]` 执行。脚本不联网、不登录、不上传；新装复制完整 Skill，相同内容重复安装仅验证，更新时备份旧目录并保留其中的 `local.json` 和其他本机文件。不要删除备份或覆盖其他 Skill。失败时报告具体阻碍，不声称成功。
5. 根据安装器输出确认 `installed`、`updated` 或 `unchanged`，记录版本、最终路径和备份路径；检查 `timezone_ready`。使用专用安装接口时也需用选定 Python 执行安装目录下 `scripts/sync_strava.py --help` 与 `sports`，核对成功并验证所需时区。不要使用 `prepare`、`review`、文件上传或真实账号数据测试安装。
6. 核对宿主是否已发现该 Skill；若宿主需要新回合、重新加载或重启，遵照实际说明告诉用户。报告“文件已安装”和“宿主已加载”的实际状态，不混为一谈。

宿主没有自动技能发现功能时，按其支持方式在持久目录安装，并提供加载最终 `SKILL.md` 的路径；说明以后需要显式加载。如果宿主完全不支持本地执行或持久文件，只说明缺少的能力，不宣称已完成可用安装。

## 安装与首次同步分开

安装不要求用户提供 Strava ID、运动日期、健康文件或浏览器登录，也不创建带占位账号的 `sync_config.json`。包内 `sync_config.example.json` 仅是格式示例。

用户首次要求同步时，再按已安装 Skill 的 `references/harness.md` 创建独立、持久的数据项目：核对实际账号，取得日期范围，运行 `scripts/setup_project.py`，记录项目路径。数据不要放入 Skill 安装目录或临时解压目录。既有项目沿用配置和清单，不重置。

以后使用时，必须完整查重、在对话中列出全部疑似项并等待用户确认，再上传未排除项；排除项记为 `exist`，实际成功项记为 `uploaded`。安装请求不授权上传。

## 完成后告诉用户

简洁报告版本、安装位置、验证结果和宿主要求的加载步骤。说明下一步把解压的健康导出放入数据项目的 `apple_health_export/`，并说“使用 apple-health-strava-sync，同步某日期范围的某种运动，先查重等我确认”。

完整功能说明见 `README_USER.md`。安装过程不需要访问 GitHub，也不需要 Git。
