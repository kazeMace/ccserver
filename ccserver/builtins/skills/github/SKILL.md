---
name: github
description: 使用 gh CLI 操作 GitHub：issues、PR、CI checks、release，以及 git 工作流规范。
tags: [github, git, pr, ci]
---

# GitHub 操作指南

## 前置条件

使用前确认 `gh` 已登录：
```bash
gh auth status
```

未登录时让用户运行：`! gh auth login`

## 常用操作

### 查看 PR / Issue

```bash
# 查看当前分支的 PR
gh pr view

# 列出所有 open PR
gh pr list

# 查看 issue
gh issue view 123

# 查看 PR 的 CI checks
gh pr checks
```

### 创建 PR

```bash
gh pr create \
  --title "feat: 添加 XXX 功能" \
  --body "$(cat <<'EOF'
## Summary
- 做了什么

## Test plan
- [ ] 运行了单元测试
- [ ] 手动验证了 XXX 场景
EOF
)"
```

### 操作 Issues

```bash
# 创建 issue
gh issue create --title "bug: XXX" --body "复现步骤..."

# 关闭 issue
gh issue close 123 --comment "已在 PR #456 中修复"

# 给 issue 打标签
gh issue edit 123 --add-label "bug,high-priority"
```

### CI / Actions

```bash
# 查看最近的 workflow runs
gh run list --limit 5

# 查看某次 run 的日志
gh run view 123456 --log

# 重新触发失败的 run
gh run rerun 123456
```

## Git 工作流规范

### commit 信息格式

```
<type>: <subject>

[可选 body]

Co-Authored-By: Claude <noreply@anthropic.com>
```

type 取值：`feat` | `fix` | `refactor` | `docs` | `test` | `chore` | `perf`

### 分支规范

- `main` / `master`：保护分支，不直接推送
- `feat/xxx`：新功能
- `fix/xxx`：bug 修复
- `chore/xxx`：工具、配置、依赖

### 危险操作确认清单

推送前必须确认：
- [ ] `git diff main...HEAD` 检查所有变更
- [ ] 没有包含 `.env`、credentials、密钥
- [ ] CI 在本地跑通（或已知 CI 失败原因）
- [ ] force push 前已和用户确认

## API 查询（gh api）

```bash
# 获取 PR 评论
gh api repos/OWNER/REPO/pulls/123/comments

# 获取 issue 列表（JSON 输出）
gh api repos/OWNER/REPO/issues --jq '.[].title'
```
