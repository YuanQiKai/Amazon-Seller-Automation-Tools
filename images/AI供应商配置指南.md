# AI 供应商配置指南（V2.4）

## 1. 先理解认证边界

ChatGPT/Codex 登录用于 OpenAI 自己的客户端体验；本地 Python 程序没有受支持的方式直接复用当前 ChatGPT/Codex 会话来调用图片生成。OpenAI 的程序化工作流使用 Platform API Key。请勿从 Codex 配置、浏览器 Cookie 或系统凭据中提取登录令牌。

官方参考：

- [Codex authentication](https://developers.openai.com/codex/auth)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2)

## 2. 文案供应商预设

| 供应商 | 协议适配器 | 默认 Base URL | Key 环境变量 | 示例模型 |
|---|---|---|---|---|
| OpenAI | `responses` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-5.6-terra` |
| CUN.AI | `chat_completions` | `https://www.cun.ai/v1` | `CUNAI_API_KEY` | `claude-fable-5` |
| 阿里云百炼 | `chat_completions` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` | `qwen-plus` |
| 火山方舟 | `responses` | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` | `doubao-seed-2-0-lite-260215` |
| 智谱 GLM | `chat_completions` | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` | `glm-4.7` |
| SiliconFlow | `chat_completions` | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` | `Qwen/Qwen3-8B` |
| 百度千帆 | `chat_completions` | `https://qianfan.baidubce.com/v2` | `QIANFAN_API_KEY` | `ernie-4.5-turbo-128k` |
| 自定义 | 两种文本适配器可选 | 用户填写 | `CUSTOM_TEXT_API_KEY` | 用户填写 |

文案模型必须能够严格返回 JSON。若供应商返回 Markdown 代码块，程序会尝试提取其中的 JSON；字段缺失仍会判定失败。

## 3. 图片供应商预设

| 供应商 | 协议适配器 | 默认 Endpoint | Key 环境变量 | 示例模型 |
|---|---|---|---|---|
| OpenAI GPT Image | `openai_images` | `/images/generations` | `OPENAI_API_KEY` | `gpt-image-2` |
| CUN.AI 图片生成 | `openai_images_url` | `/images/generations` | `CUNAI_API_KEY` | 从账户 `/models` 动态选择 |
| 阿里云通义万相 | `dashscope_wan` | `/services/aigc/multimodal-generation/generation` | `DASHSCOPE_API_KEY` | `wan2.6-t2i` |
| 火山 Seedream | `openai_images_url` | `/images/generations` | `ARK_API_KEY` | `doubao-seedream-4-5-251128` |
| 智谱 CogView | `openai_images_url` | `/images/generations` | `ZHIPU_API_KEY` | `cogview-4-250304` |
| SiliconFlow | `siliconflow_images` | `/images/generations` | `SILICONFLOW_API_KEY` | `Qwen/Qwen-Image` |
| 自定义 | 常见图片适配器可选 | 用户填写 | `CUSTOM_IMAGE_API_KEY` | 用户填写 |

程序可识别以下常见响应：`data[0].b64_json`、`data[0].url`、`images[0].b64_json`、`images[0].url` 及部分 DashScope 输出，并统一转存为 JPG。

参考图能力因模型而异：OpenAI 路径使用图片编辑接口；其他适配器按供应商兼容字段提交 base64 图片。若所选模型不接受参考图，请切换支持图生图/图片编辑的模型，或新增专用协议适配器。

## 4. 文件化供应商配置

AI 设置页只显示供应商与模型下拉框。所有连接参数集中在 `amazon_image_brief/resources/ai_providers.json`，每个供应商维护：

1. `protocol` 协议适配器；
2. `base_url`、调用 `endpoint` 与模型列表 `model_endpoint`；
3. `api_key_env` 环境变量名；
4. `models` 默认/回退模型列表；
5. 可选 `extra_headers` JSON 对象。

修改文件后需要重启程序。自定义并不表示可以适配任意私有协议；如服务要求签名算法、异步轮询、特殊上传或不同 JSON 请求体，需要在 `amazon_image_brief/ai_client.py` 中新增适配器。

### CUN.AI 当前参数

```json
{
  "label": "CUN.AI",
  "protocol": "chat_completions",
  "base_url": "https://www.cun.ai/v1",
  "endpoint": "/chat/completions",
  "model_endpoint": "/models",
  "api_key_env": "CUNAI_API_KEY",
  "models": ["claude-fable-5"]
}
```

图片供应商也包含 `cunai` 预设，Base URL 和 Key 与文案共用，Endpoint 为 `/images/generations`。官方文档明确其网关为 OpenAI Compatible 并支持 `/models`，但未公开承诺固定图片模型清单，因此配置中的图片 `models` 默认留空；必须先从界面获取账户模型并选择明确支持图片生成的模型。

## 5. 安全配置

推荐在项目根目录创建不会提交到版本库的 `.env`：

```dotenv
OPENAI_API_KEY=
CUNAI_API_KEY=
DASHSCOPE_API_KEY=
ARK_API_KEY=
ZHIPU_API_KEY=
SILICONFLOW_API_KEY=
QIANFAN_API_KEY=
CUSTOM_TEXT_API_KEY=
CUSTOM_IMAGE_API_KEY=
```

不要把真实 Key 写进供应商 JSON、Prompt、项目 JSON、Excel、截图或源码。用户给出的示例字符串 `apikey` 视为占位符；实际调用前必须在 `.env` 中填写控制台生成的真实 Key。

## 6. V2.4 连接、模型、切换与费用

- “获取全部模型”向供应商模型目录发送鉴权 GET 请求，验证网络、Base URL、Key 和账户权限。
- 文案“测试连接”先读取模型目录，再向所选模型发送一条极短对话，因此会产生少量 token；图片测试只读取模型目录，不生成图片。
- “获取全部模型”用供应商返回的模型 ID 更新模型下拉框。部分供应商或自定义网关没有兼容 `/models` 接口时，应在 `ai_providers.json` 的 `models` 数组补充模型 ID 后重启程序。
- 开启自动切换后，主供应商在重试后仍失败才会调用备用供应商。文案和图片分别配置一套备用供应商及模型；备用 Key 从其预设环境变量读取。
- 每分钟请求上限在同一进程内按供应商共享，批量队列与单项目调用共同受限。缓存按供应商、协议、地址、模型、请求内容及参考图/蒙版哈希命中，不包含 API Key；局部重生强制绕过缓存。
- 费用预估按模块数、预计 token 和内置单价计算，不等于最终账单。OpenAI 图片按图像 token 计费，界面的单图数值属于保守规划值；未知模型会明确显示“费率未配置”。
- OCR、图片文字合规和六语回译会增加文案模型调用，因此默认关闭；启用后应先运行费用预估。

## 7. 蒙版局部重生

在六语审核页选择一个已经生成图片的模块，点击“蒙版选区式重生”，用画笔涂抹需要替换的区域。程序会保存透明 PNG 蒙版并传给支持图片编辑的适配器。蒙版与编辑底图必须同尺寸；不支持蒙版的模型可能忽略该字段或返回错误，此时请切换到明确支持图片编辑的模型。

## 8. 官方供应商资料

- [阿里云百炼文本生成与 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/text-generation)
- [阿里云百炼图像生成 API](https://help.aliyun.com/en/model-studio/text-to-image-v2-api-reference)
- [火山方舟 API 快速开始](https://www.volcengine.com/docs/82379/1795150)
- [火山 Seedream 图片生成](https://www.volcengine.com/docs/6492/2221472?lang=zh)
- [智谱 CogView](https://docs.bigmodel.cn/cn/guide/models/image-generation/cogview-4)
- [SiliconFlow 图片生成](https://docs.siliconflow.com/en/api-reference/images/images-generations)
- [百度千帆 Chat Completions](https://cloud.baidu.com/doc/qianfan-api/s/3m7of64lb)
- [CUN.AI SDK 接入](https://doc.cun.ai/zh/guide/clients/sdk)
- [CUN.AI 故障排查与模型列表测试](https://doc.cun.ai/zh/troubleshooting)

模型 ID、区域域名、价格、配额和接口能力会更新。正式使用前请在对应控制台确认当前模型 ID，并先用低质量或单张任务验证成本与返回结构。
