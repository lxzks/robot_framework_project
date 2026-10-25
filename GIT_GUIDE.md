# GIT_GUIDE.md — 从 SSH 密钥开始，用 Git 管好这个项目

> **这份文档按"从头来一遍"的顺序写。** 每一步都是"你输入什么 → 应该看到什么 → 出错怎么办"。
>
> ⚠️ **但先看第 0 节** —— 有几样东西你**已经有了**，跳过能省 20 分钟。

---

## 0. 你现在的真实状态（先确认，避免重复劳动）

| 项目 | 状态 | 结论 |
|---|---|---|
| SSH 密钥 | ✅ 已有 `~/.ssh/id_ed25519`（2025-07-05 创建） | **不用重新生成** |
| 密钥是否已配到 GitHub | ✅ **已配好**（测试输出 `Hi lxzks! You've successfully authenticated`） | **第 1 节可以直接跳过** |
| GitHub 用户名 | `lxzks` | 配置 git 身份时用这个 |
| 邮箱（与密钥关联） | `1520313676@qq.com` | 配置 git 身份时用这个 |
| SSH 客户端 | ✅ OpenSSH 10.3 | 正常 |
| Git 版本 | ✅ 2.55.0（WorkBuddy 自带 PortableGit） | 正常 |
| 本地仓库 | ⚠️ 已 `git init`，但**没有任何提交**（`.git` 只有 61K） | 可以安全删掉重来 |
| 远程仓库 | ❌ 还没有 | 第 5 节去 GitHub 建 |

> 💡 **一句话**：SSH 那块你其实已经毕业了。第 1 节是"完整知识"（想学就看），
> **实际动手从第 2 节开始**。

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
| `failed to push some refs` / `Updates were rejected` | 远程有你本地没有的提交 | `git pull --rebase` 再 push |
| `error: Your local changes would be overwritten` | 有未提交改动 | 先 commit 或 `git stash` |
| 中文文件名显示 `\346\226\207` | 没设 quotepath | 3.2 节 |
| 每次提交都显示"全部文件被改" | 换行符问题 | 3.2 节 |
| `fatal: pathspec ... did not match` | 文件名/路径错 | `ls` 确认 |

> 💡 你的 git 是 **PortableGit**，`git xxx --help` 会报 "documentation file not found"。
> 查命令列表用 `git help -a`，细节看这份文档。

---

## 11. 一页速查

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
