# 鲸吸购控制台（Mac 安装包）

## 打包安装程序

在项目根目录已安装 Node.js（建议 18+）的前提下执行：

```bash
cd clients/mac-electron
npm install
npm run build:installer
```

产物目录：**`dist/`**

| 文件 | 说明 |
|------|------|
| **`鲸吸购控制台-1.0.0-arm64.dmg`**（或 `x64`） | 常见分发方式：双击挂载，将应用拖入「应用程序」 |
| **`鲸吸购控制台-1.0.0-arm64.pkg`** | 系统安装向导，适合企业内批量安装 |
| **`鲸吸购控制台-1.0.0-arm64.zip`** | 解压即用，无需挂载镜像 |

当前构建为**本机 CPU 架构**（Apple 芯片为 arm64，Intel 为 x64）。若需另一种架构，可在另一台机器上打包，或查阅 `electron-builder` 文档配置 `arch`。

## 仅打某一种包

```bash
npm run build:dmg   # 仅 DMG
npm run build:pkg   # 仅 PKG
```

## 未签名说明

默认**不做 Apple 开发者签名**。首次打开可能出现「无法验证开发者」：在 **系统设置 → 隐私与安全性** 中选择仍要打开，或右键应用 → 打开。

正式分发可申请 Apple Developer 账号，在 `package.json` 的 `build.mac` 中配置 `identity` 与公证流程。

## 使用

安装后启动应用，菜单 **鲸吸购 → 服务端地址…** 填写运行 `xianyu-good` 服务的地址（如 `http://192.168.1.8:8000`）。
