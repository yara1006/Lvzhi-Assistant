# 贡献指南

感谢你对律智助手项目的关注！

## 开发环境搭建

1. Fork 本仓库并 clone 到本地
2. 安装依赖：`make install`
3. 复制环境变量：`cp .env.example .env` 并填入你的 API Key
4. 启动开发服务器：`make dev`
5. 运行测试：`make test`

## 提交规范

本项目使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 文档变更
- `refactor:` 代码重构
- `test:` 测试相关
- `chore:` 构建/工具变更

## PR 流程

1. 创建你的功能分支：`git checkout -b feat/my-feature`
2. 提交变更：`git commit -m "feat: add xxx"`
3. 推送分支：`git push origin feat/my-feature`
4. 提交 Pull Request

## 代码风格

- Python：遵循 PEP 8，使用 ruff 检查
- JavaScript：遵循项目现有风格
- 提交前运行 `pytest` 确保测试通过
