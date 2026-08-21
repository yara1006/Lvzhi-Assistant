# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/) 规范。

## [1.0.0] - 2026-08-21

### 新增
- 法律对话功能（腾讯元器 / 混元 OpenAPI）
- 法条检索功能（得理法搜 API）
- 合同生成（15 种合同模板）
- 合同审查（文件上传 + 文本输入）
- 模拟法庭预演（独立 React 应用）
- JWT 认证 + 手机号验证码登录
- Docker Compose 一键部署
- 完整后端测试套件

### 修复
- 移除硬编码 API 密钥和服务器 IP
- 修复 CORS 配置
- 修复前端 XSS 风险
- 消除重复代码
- 清理调试日志
