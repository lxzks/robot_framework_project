# GIT_GUIDE.md — 从 SSH 密钥开始，用 Git 管好这个项目

> **这份文档按"从头来一遍"的顺序写。** 每一步都是"你输入什么 → 应该看到什么 → 出错怎么办"。
>
> ⚠️ **但先看第 0 节** —— 有几样东西你**已经有了**，跳过能省 20 分钟。

---

## 0. 你现在的真实状态（2026-10-25 更新：**已成功推送到 GitHub** ✅）

| 项目 | 状态 |
|---|---|
| SSH 密钥 | ✅ 已有 `~/.ssh/id_ed25519`，且已配到 GitHub（`Hi lxzks!` 测试通过） |
| GitHub 用户名 | `lxzks` |
| 邮箱 | `1520313676@qq.com` |
| git 身份 | ✅ 已配置（全局） |
| 本地仓库 | ✅ 已 init，分支已改名 **main** |
| 提交历史 | ✅ `9d04a00`(merge) ← `1657fee new commit` + `ae49d80 Initial commit` |
| 远程仓库 | ✅ `git@github.com:lxzks/robot_framework_project.git` |
| 同步状态 | ✅ 本地 HEAD == origin/main（**122 个文件已推送**） |
| 分支跟踪 | ✅ `main → origin/main` |

> 🎉 **结论：你已经完成了从零到推送的全流程。** 下面第 1~6 节是"完整知识回顾",
> **实际动手从第 7 节"日常循环"开始**。

**但留了一个小尾巴**（第 11 节坑 5 会讲）：
本地的第一个提交信息是 `new commit` —— 不规范。**面试官会点开看**。
不用回头改（改要 force push，风险大于收益），**从下一次提交开始写规范就行**。

---

## 1. SSH 密钥（完整步骤 —— 你已完成，可直接跳到第 2 节）

### 1.1 先搞清一个误解

> ❌ **不是"在 GitHub 网站上生成密钥"**
> ✅ **密钥在你自己的电脑上生成**，然后把**公钥**贴到 GitHub 网站。

**为什么这样设计**：
- **私钥**（`id_ed25519`）永远留在你电脑，**绝不外传**
- **公钥**（`id_ed25519.pub`）可以公开，贴到 GitHub
- push 时 GitHub 用公钥验证"你手里有对应的私钥" → 证明是你本人

比喻：**公钥像一把锁，私钥像唯一能开它的钥匙**。你把锁给 GitHub，钥匙自己留着。

### 1.2 检查是否已有密钥

```bash
ls -la ~/.ssh/
```

**已有的话会看到**：
```
id_ed25519          ← 私钥（权限应该是 -rw-------）
id_ed25519.pub      ← 公钥（可以公开）
known_hosts         ← 连过的主机记录
```

> 💡 `~` 在 Git Bash 里就是 `C:\Users\你的用户名`。

### 1.3 生成新密钥（**你不需要做，仅作知识**）

```bash
ssh-keygen -t ed25519 -C "1520313676@qq.com"
```

**会连续问三个问题**：

| 提示 | 怎么答 | 说明 |
|---|---|---|
| `Enter file in which to save the key` | **直接回车** | 用默认路径 `~/.ssh/id_ed25519` |
| `Enter passphrase` | **直接回车**（=不设密码） | 设了更安全，但每次用都要输 |
| `Enter same passphrase again` | 再回车 | |

> 💡 `-t ed25519` 是密钥算法（比老 RSA 更短更安全，GitHub 推荐）；
> `-C` 是注释，一般写邮箱，方便以后认出这把钥匙是谁的。

**成功会看到**：
```
Your identification has been saved in /c/Users/xxx/.ssh/id_ed25519
Your public key has been saved in /c/Users/xxx/.ssh/id_ed25519.pub
The key fingerprint is:
SHA256:xxxxx 1520313676@qq.com
```

> ⚠️ **如果生成时提示文件已存在**：说明你已经有了（就是你这种情况），别覆盖。

### 1.4 启动 ssh-agent 并加载私钥（重装系统/重启后才需要）

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

**成功看到**：`Agent pid xxxx` 和 `Identity added: ...`

> 💡 作用：把私钥加载到内存，避免每次操作都读文件。
> **如果 `ssh -T` 测试能通，这一步可以跳过。**

### 1.5 把公钥贴到 GitHub

**第一步：复制公钥全文**

```bash
cat ~/.ssh/id_ed25519.pub
```

输出是**一整行**（以 `ssh-ed25519` 开头，以邮箱结尾）：
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGRruhT3nm/MzEODogz3PPlxKSOrhBy/d6pAozzYq/Q8 1520313676@qq.com
```

**全选复制这一整行**（从 `ssh-ed25519` 到邮箱，别漏、别多空格）。

**第二步：在 GitHub 上添加**

1. 打开 https://github.com/settings/keys
2. 点 **New SSH key**
3. **Title**：随便起（如 `Windows-工作电脑`）
4. **Key type**：`Authentication Key`
5. **Key**：粘贴刚才那一行
6. 点 **Add SSH key**（可能要输 GitHub 密码确认）

### 1.6 测试连接（**关键验证**）

```bash
ssh -T git@github.com
```

**第一次会问**：
```
The authenticity of host 'github.com (20.205.243.166)' can't be established.
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```
→ 输入 **`yes`** 回车（只问一次）

**成功的标志**：
```
Hi lxzks! You've successfully authenticated, but GitHub does not provide shell access.
```

> ✅ **看到 `Hi 你的用户名!` 就说明 SSH 配好了。**
>
> ⚠️ `Permission denied (publickey)` → 公钥没贴对，重走 1.5。
> ⚠️ 卡住不动 → 网络问题，试 `ssh -T -p 443 git@ssh.github.com`。

**你的实际结果**：✅ 已通过（`Hi lxzks!`）→ **第 1 节全部跳过**。

---

## 2. 删掉旧仓库，从头开始

### 2.1 先确认删掉不会丢东西

```bash
cd "C:/Users/sen.kang/Desktop/文档/项目/兴趣/robot_framework"
git log --oneline
```

**你的输出**：
```
fatal: your current branch 'master' does not have any commits yet
```

> ✅ **没有任何提交** → 删 `.git` 只是删"版本管理记录"，**代码/文档一个都不会少**。

### 2.2 删除（**不可逆，但对你安全**）

```bash
rm -rf .git
```

**验证**：
```bash
ls -a
```
应该**看不到 `.git` 目录**了（`.gitignore` 还在，它不是目录）。

> 🔴 **警告**：如果 `git log` 显示**有提交记录**，**绝对不要删** —— 会丢掉历史。
> 你现在没有提交，所以安全。

### 2.3 确认项目文件还在

```bash
ls
```
应该还能看到 `README.md`、`docs/`、`simulators/` 等 —— **代码没动**。

---

## 3. 配置 git 身份（**必做**）

### 3.1 设置名字和邮箱

**必须与你 GitHub 账号一致**（否则提交在 GitHub 上不关联到你、不显示头像）：

```bash
git config --global user.name "lxzks"
git config --global user.email "1520313676@qq.com"
```

> 💡 `--global` = 这台电脑所有仓库都用这个身份。
> 只想给本项目设 → 去掉 `--global`（在项目目录里执行）。

### 3.2 三个 Windows 必做设置

```bash
git config --global init.defaultBranch main    # 默认分支叫 main
git config --global core.autocrlf true         # 换行符（不配的话每次提交都显示"文件全改了"）
git config --global core.quotepath false       # 中文文件名不乱码（否则显示成 \346\226\207）
```

### 3.3 验证

```bash
git config --global --list | grep -E "user|autocrlf|quotepath|defaultbranch"
```

**应该看到 5 行**：
```
user.name=lxzks
user.email=1520313676@qq.com
core.autocrlf=true
core.quotepath=false
init.defaultbranch=main
```

---

## 4. 第一次提交

### 4.1 初始化仓库

```bash
git init
```

**应该看到**：
```
Initialized empty Git repository in C:/Users/sen.kang/Desktop/文档/项目/兴趣/robot_framework/.git/
```

> 💡 如果看到 `Reinitialized existing Git repository` → 说明 `.git` 没删干净（回第 2 节）。

### 4.2 看清单（**每次提交前都做**）

```bash
git status
```

**应该看到**：
```
On branch main

No commits yet

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        .github/
        .gitignore
        GIT_GUIDE.md
        README.md
        coverage_report.py
        deliverables/
        docs/
        ...
```

**🚨 重点检查（这一步能救你）**：列表里**绝对不能出现**：

| 不该出现 | 为什么 |
|---|---|
| `.venv/` | 虚拟环境，5000+ 文件、几百 MB |
| `results/` | 测试产物，每次生成 |
| `.workbuddy/` | **你的私人工作日志** |
| `__pycache__/` | Python 缓存 |
| `simulators/dashboard_state.json` | 运行时状态 |
| `*.tmp` | 临时残留 |

**出现了 → 停下告诉我**（你的 `.gitignore` 已配好，正常不会出现）。

### 4.3 暂存所有文件

```bash
git add .
```

**再检查一次**：
```bash
git status --short | head -15
```
现在应该都是 `A`（Added）状态。

### 4.4 提交

```bash
git commit -m "feat: 车载自动化测试平台初始版本

- 三节点仿真：动力域 ECU + 数字仪表 + 安卓车机（UDP 组播虚拟 CAN）
- Robot Framework 测试：116 用例（主套件 62 + 真实场景 54）
- 需求追溯：63 条需求，覆盖率 100%，自动生成追溯矩阵
- 工程化：DBC 驱动用例生成、覆盖率统计、交付报告、CI
- 文档：22 章教程 + 7 天零基础主线 + 8 道 Python 练习"
```

**应该看到**（`a1b2c3d` 是提交哈希，你的会不同）：
```
[main (root-commit) a1b2c3d] feat: 车载自动化测试平台初始版本
 130 files changed, 25000 insertions(+)
 create mode 100644 .gitignore
 create mode 100644 README.md
 ...
```

> 💡 **为什么写多行**：面试官会点开 commit 看。
> `feat:` 开头是业界规范（feat/fix/docs/test/refactor/chore）。

### 4.5 验证

```bash
git log --oneline
```
**应该看到**：`a1b2c3d feat: 车载自动化测试平台初始版本`

```bash
git branch
```
**应该看到**：`* main`（星号 = 当前分支）

---

## 5. 在 GitHub 建仓库（网页操作）

### 5.1 建仓库

1. 打开 https://github.com/new
2. **Repository name**：`vehicle-test-framework`（名字随你）
3. **Description**：`车载自动化测试平台：ECU/仪表/车机仿真 + 116 用例 + 需求追溯`
4. **Public**（面试官要能看）
5. 🔴 **三个勾选框全部不勾**：
   - ❌ Add a README file
   - ❌ Add .gitignore
   - ❌ Choose a license
6. 点 **Create repository**

> ⚠️ **为什么不能勾**：你本地已有 README 和 .gitignore，勾了会生成远程文件，
> push 时报 `failed to push some refs`。

### 5.2 复制 SSH 地址（**不是 HTTPS**）

建好后页面会显示仓库地址，**点 "SSH" 标签**，复制：
```
git@github.com:lxzks/vehicle-test-framework.git
```

### 5.3 关联远程仓库

```bash
git remote add origin git@github.com:lxzks/vehicle-test-framework.git
```

**验证**：
```bash
git remote -v
```
**应该看到两行**：
```
origin  git@github.com:lxzks/vehicle-test-framework.git (fetch)
origin  git@github.com:lxzks/vehicle-test-framework.git (push)
```

---

## 6. 推送（第一次）

```bash
git push -u origin main
```

**应该看到**：
```
Enumerating objects: 130, done.
Counting objects: 100% (130/130), done.
...
To github.com:lxzks/vehicle-test-framework.git
 * [new branch]      main -> main
branch 'main' set up to track 'origin/main'.
```

> 💡 SSH 已配好，**不会弹登录窗口**，直接传。
> 💡 `-u` = 把本地 `main` 绑定到远程 `origin/main`。以后直接 `git push`。

**验证**：刷新 GitHub 页面 → 文件都在 → ✅ 完成。

---

## 7. 日常循环（以后就这 4 条）

```bash
git status                  # ① 看改了什么
git add -A                  # ② 全部暂存
git commit -m "fix: xxx"    # ③ 提交
git push                    # ④ 推上去
```

**开工前**（多人协作时）：`git pull`

### 提交信息怎么写

```
fix: 修复清除 DTC 后数量不归零的竞态

根因：故障条件持续存在时，ECU 下一个检测周期会重新记录 DTC，
导致"清除后立刻读数量"偶发失败。
修法：清除前用 85 02 暂停 DTC 检测。
```

**三段**：`类型: 摘要` + 空行 + `原因/做法`。

---

## 8. 最常用操作（按场景查）

### 8.1 查看

| 我想 | 命令 |
|---|---|
| 看状态 | `git status` |
| 看精简状态 | `git status --short` |
| 看改了哪些行 | `git diff` |
| 看已暂存的改动 | `git diff --staged` |
| 看历史 | `git log --oneline` |
| 看历史（带图） | `git log --oneline --graph --all` |
| 看某次提交 | `git show <commit-id>` |
| 看某文件历史 | `git log --follow -p <file>` |

### 8.2 撤销（**最容易搞混，重点看**）

| 场景 | 命令 | 风险 |
|---|---|---|
| 改坏了，还原某文件（未 add） | `git restore <file>` | ⚠️ 改动丢失 |
| add 错了，取消暂存 | `git restore --staged <file>` | 安全 |
| commit 信息写错（未 push） | `git commit --amend -m "新信息"` | 安全 |
| 漏了文件，补进上次提交 | `git add <file>` + `git commit --amend --no-edit` | 安全 |
| 撤销最后一次 commit，保留改动 | `git reset --soft HEAD~1` | 安全 |
| 撤销最后一次 commit，改动也不要 | `git reset --hard HEAD~1` | 🔴 **危险** |
| **已 push，想撤销某次提交** | `git revert <commit-id>` | 安全（生成反向提交） |
| 临时存一下手头的活 | `git stash` → `git stash pop` | 安全 |

> 🔴 **铁律**：**已 push 的提交不要用 `reset --hard`**，用 `revert`。
> 否则别人拉下来会和你的历史冲突。

### 8.3 分支

```bash
git branch                      # 看所有分支
git switch -c feature-xxx       # 新建并切换
git switch main                 # 切回 main
git merge feature-xxx           # 把分支合并到当前分支
git branch -d feature-xxx       # 删除已合并的分支
```

**什么时候用**：改一个**可能失败**的大功能时。失败了切回 main，不留痕迹。

### 8.4 远程

```bash
git remote -v                           # 看远程地址
git remote set-url origin <新地址>       # 改地址（HTTPS → SSH 等）
git pull                                # 拉最新
git pull --rebase                       # 拉最新（历史更干净）
git push                                # 推
git clone <地址>                        # 克隆别人的仓库
```

---

### 8.5 怎么查 / 改 / 切换"关联的是哪个仓库"

**先记一个核心概念**：

> ❌ 不存在"关联到 GitHub 账号"这回事
> ✅ 只关联到**某个具体仓库的地址**。`origin` 只是这个地址的名字。

```
你的 GitHub 账号 lxzks
├── repo-A                    ← 地址：git@github.com:lxzks/repo-A.git
├── repo-B
└── vehicle-test-framework    ← 这个项目只能指向其中一个

本地文件夹 robot_framework/.git
    └── origin = git@github.com:lxzks/robot_framework_project.git
```

**三个查看命令**：

| 我想知道 | 命令 |
|---|---|
| 这个项目关联了哪个远程？ | `git remote -v` |
| `origin` 具体指向哪个地址？ | `git config --get remote.origin.url` |
| 本地分支在跟踪哪个远程分支？ | `git branch -vv` |

**空输出说明什么**：`git remote -v` 没输出 = **这个项目还没关联任何远程仓库**（`git push` 会报错）。

**关联到某个仓库（核心就这一条）**：

```bash
git remote add origin git@github.com:lxzks/【仓库名】.git
                                          # ↑ 这个仓库名决定关联到哪一个
```

**关联错了怎么改**（不用删仓库）：

```bash
git remote set-url origin git@github.com:lxzks/正确的仓库名.git
```

**一个本地仓库能不能关联多个远程？**

可以，但要起不同名字：

```bash
git remote add backup git@github.com:lxzks/另一个仓库.git
git push backup main        # 推到 backup
git push origin main        # 推到 origin（默认）
```

> 💡 **新手不建议**——一个 `origin` 最不容易搞错。

**跟着文件夹走的原则**（防止推错仓库）：

```bash
pwd              # ① 先确认自己在哪个目录
git remote -v    # ② 再确认这个目录指向哪个仓库
```

**在哪个文件夹里执行 `git` 命令，就操作哪个仓库**。
你的 `API_Gen`、`deepseek-harness` 各管各的，互不影响。

---

## 9. 这个项目的铁律

### 9.1 绝不提交

| 文件/目录 | 原因 |
|---|---|
| `.workbuddy/` | **你的私人工作日志**，泄露很麻烦 |
| `.venv/` | 5000+ 文件、几百 MB，用 `requirements.txt` 重建 |
| `results/` | 每次跑测试重新生成 |
| `simulators/dashboard_state.json` | 运行时状态，每次变 |
| `__pycache__/` | 机器相关缓存 |
| `*.tmp` | 临时残留 |

**已在 `.gitignore` 配好 —— 别删这些规则。**

### 9.2 每次提交前 10 秒检查

```bash
git status --short
```

**扫一眼**：
- `??` 里出现 `.venv` / `results` / `.workbuddy` → **停下，.gitignore 出问题了**
- 出现不认识的文件 → 想清楚该不该进仓库

### 9.3 提交频率

- 一个完整小改动 = 一次提交（别攒一周）
- 每次提交代码是**能跑的**
- 提交信息写**为什么改**

---

## 10. 常见错误对照表

| 报错 | 原因 | 解决 |
|---|---|---|
| `Please tell me who you are` | 没配 user.name/email | 第 3 节 |
| `fatal: not a git repository` | 不在项目目录 | `cd` 到项目根目录 |
| `Permission denied (publickey)` | SSH 公钥没贴对 | 重走 1.5 |
| `Host key verification failed` | known_hosts 有问题 | `ssh-keygen -R github.com` 后重连 |
| `remote: Repository not found` | 地址错 / 没权限 | `git remote -v` 检查 |
| `error: src refspec main does not match any` | **本地分支名和你推的名字不一样**（本地叫 `master`，你推 `main`） | `git branch -M main` 再推；或直接 `git push -u origin master` |
| `failed to push some refs` / `Updates were rejected` | 远程有你本地没有的提交（比如建仓库时勾了 README/LICENSE） | 见第 12 节"坑 4"：`git pull origin main --allow-unrelated-histories --no-edit` 再推 |
| `error: Your local changes would be overwritten` | 有未提交改动 | 先 commit 或 `git stash` |
| 中文文件名显示 `\346\226\207` | 没设 quotepath | 3.2 节 |
| 每次提交都显示"全部文件被改" | 换行符问题 | 3.2 节 |
| `fatal: pathspec ... did not match` | 文件名/路径错 | `ls` 确认 |

> 💡 你的 git 是 **PortableGit**，`git xxx --help` 会报 "documentation file not found"。
> 查命令列表用 `git help -a`，细节看这份文档。

---

## 11. 实战踩坑记录（真实遇到的 5 个问题）

> 这一节是**你第一次推到 GitHub 时实际踩到的坑**。每个都给"现象 → 原因 → 解决"。
> 比抽象的命令讲解有用得多 —— 因为下次你（或同事）会再遇到。

### 坑 1：`git add` 时刷屏 `LF will be replaced by CRLF`

**现象**：
```
warning: in the working copy of 'coverage_report.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'docs/07_CAN仿真测试实战.md', LF will be replaced by CRLF ...
（刷了几十行）
```

**这是什么**：**正常提示，不是错误**。

- 你配了 `core.autocrlf true`（Windows 标准配置）
- 含义：提交时 CRLF → LF（统一存进仓库），检出时 LF → CRLF（适配 Windows）
- 你的文件当前是 LF，git 提醒"下次检出时会转成 CRLF"

**怎么处理**：**什么都不用做**。

| 会不会 | 答案 |
|---|---|
| 影响 `git add` 成功？ | ❌ 不会（你实际成功暂存了 121 个文件） |
| 影响提交内容？ | ❌ 不会（仓库里存的是 LF，跨平台一致） |
| 影响代码运行？ | ❌ 不会（Python 两种换行都认） |
| 影响什么？ | 只是刷屏，让你以为出错了 |

> 💡 **判断标准**：全是 `warning:` 且没有一行 `error:` / `fatal:` → 可以忽略。
> 而且**第一次 add 之后基本不再出现**。

**如果实在想消除**（可选）：项目根目录建 `.gitattributes`，内容：
```
* text=auto
```

---

### 坑 2：`git status` 显示 `nothing to commit, working tree clean` —— 是不是没生效？

**现象**：`git add .` + `git commit` 之后，`git status` 显示：
```
On branch master
nothing to commit, working tree clean
```

**这是什么**：**这是成功的样子**。意思是"工作区里没有任何未提交的改动"——所有文件都存好了。

**验证方法**：
```bash
git log --oneline
```
能看到提交记录（如 `1657fee feat: ...`）= 确实提交成功了。

**`git status` 的四种输出，一眼看懂**：

| 输出 | 意思 | 要做什么 |
|---|---|---|
| `nothing to commit, working tree clean` | **全部已提交，干净** | ✅ 什么都不用做 |
| `Untracked files:` + 列表 | 有新文件**还没纳入版本管理** | `git add .` |
| `Changes not staged for commit:` | 文件**改了但没暂存** | `git add .` |
| `Changes to be committed:` | 已暂存，**等着 commit** | `git commit -m "..."` |

> 💡 新手最常见的误解：以为"没东西可提交"= "出错了"。
> **实际相反** —— 这是最理想的状态。

---

### 坑 3：`error: src refspec main does not match any`

**现象**：
```bash
$ git push -u origin main
error: src refspec main does not match any
error: failed to push some refs to 'github.com:lxzks/robot_framework_project.git'
```

**原因**：**你本地分支叫 `master`，但你推的是 `main`**。
本地没有 `main` 这个分支 → git 找不到 → 报 "refspec 不匹配"。

**怎么确认**：
```bash
git branch
```
如果你看到 `* master`（没有 main），就是这个原因。

**解决（二选一）**：

```bash
# 方案 A（推荐）：把本地分支改名为 main，再推
git branch -M main
git push -u origin main

# 方案 B：直接推 master（分支名会保持 master）
git push -u origin master
```

> 💡 **为什么推荐 A**：GitHub 现在默认用 `main`，统一了以后不容易乱。
> `git branch -M` 只是**改名**，代码和提交都不动。

---

### 坑 4：远程仓库已经有内容，push 被拒绝

**现象**：
```bash
$ git push -u origin main
! [rejected]        main -> main (fetch first)
error: failed to push some refs
hint: Updates were rejected because the remote contains work that you do not have locally.
```

**原因**：**建 GitHub 仓库时勾选了 README / .gitignore / LICENSE**，
GitHub 自动生成了一个 `Initial commit`。你本地也有一套历史 → **两边历史无关，不能直接推**。

**先看远程到底有什么**（这一步很重要，避免误删东西）：
```bash
git fetch origin main
git ls-tree -r --name-only origin/main      # 远程有哪些文件
git log origin/main --oneline -1            # 远程的提交是什么
```

**你遇到的情况**：远程只有 1 个 `LICENSE` 文件 → **合并进来就行，不用强制覆盖**：

```bash
git pull origin main --allow-unrelated-histories --no-edit
git push -u origin main
```

| 参数 | 作用 |
|---|---|
| `--allow-unrelated-histories` | 允许合并"无关的两段历史"（必须加，否则报 `refusing to merge unrelated histories`） |
| `--no-edit` | 用默认的合并信息，**不打开编辑器**（不加的话可能卡在 vim 里退不出来） |

**另一种做法（强制覆盖）**：
```bash
git push -u origin main --force
```
- 会**丢掉远程的 Initial commit**（你那个 LICENSE）
- ⚠️ **以后别随便用 `--force`**：多人协作时会冲掉别人的提交

**根本预防**：**本地已有代码时，建 GitHub 仓库三个勾选框全部不勾**。
（README / .gitignore / LICENSE 都别勾，你本地已经有了）

---

### 坑 5：第一次的 commit 信息写成了 `new commit`

**现象**：`git log --oneline` 显示：
```
1657fee new commit
```

**问题**：这条信息**什么也没说明白**。面试官点开你的仓库，看到 commit 历史是
`new commit`、`update`、`修改一下` 这种，会觉得不专业。

**现在还要不要改？**

| 情况 | 建议 |
|---|---|
| **还没 push** | `git commit --amend -m "新的信息"` 改（安全） |
| **已经 push 了** | **不建议改** —— 要 `git push --force`，风险大于收益 |

> 💡 你现在的状态是"已经 push 了"，所以**别折腾**，记住教训就够。

**从现在开始，用这个格式**：

```
类型: 一句话摘要（50 字内）

（空一行）
为什么改 / 怎么改的
```

**类型前缀**（业界通用）：

| 前缀 | 什么时候用 |
|---|---|
| `feat:` | 新增功能 |
| `fix:` | 修 bug |
| `docs:` | 只改文档 |
| `test:` | 只改测试 |
| `refactor:` | 重构（不改功能） |
| `chore:` | 杂项（改配置、.gitignore 等） |

**好例子 vs 坏例子**：

```
❌ new commit            ❌ update            ❌ 修改

✅ docs: 补充 git 实战踩坑记录
✅ fix: 修复清除 DTC 后数量不归零的竞态
   根因：故障条件持续存在时 ECU 会重新记录 DTC
   修法：清除前用 85 02 暂停 DTC 检测
```

> 💡 **判断标准**：三个月后你（或同事）看到这条信息，
> **能不能知道"改了什么、为什么改"**。不能就该重写。

---

### 这 5 个坑的共同规律

| 坑 | 本质 | 一句话记法 |
|---|---|---|
| 1 | 换行符配置的正常提示 | `warning` 可以忽略，`error` 才要管 |
| 2 | 理解 `git status` 的语义 | `clean` 是好事，不是坏事 |
| 3 | 分支名不一致 | 推之前先 `git branch` 看一眼 |
| 4 | 两边都有历史 | 建仓库时**三个勾全部不勾** |
| 5 | 提交信息不专业 | 写"为什么改"，不写"new commit" |

---

## 12. 一页速查

```bash
# ===== 一次性配置 =====
git config --global user.name "lxzks"
git config --global user.email "1520313676@qq.com"
git config --global init.defaultBranch main
git config --global core.autocrlf true
git config --global core.quotepath false

# ===== SSH 测试 =====
ssh -T git@github.com                    # 看到 Hi lxzks! 就 OK

# ===== 从零开始 =====
git init
git add .
git commit -m "feat: 初始版本"
git remote add origin git@github.com:lxzks/仓库名.git
git push -u origin main

# ===== 日常 =====
git status
git add -A
git commit -m "fix: xxx"
git push

# ===== 救急 =====
git restore <file>                  # 撤销单文件改动
git restore --staged <file>         # 取消暂存
git revert <commit>                 # 安全撤销已推送的提交
git stash / git stash pop           # 临时保存

# ===== 查看 =====
git log --oneline --graph           # 历史
git diff                            # 改了什么
git remote -v                       # 远程地址
```
