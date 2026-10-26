# gpt_image2_provider（测试中）

独立的 `gpt-image-2` 生图实验插件。它复用 GA 已配置的 Octopus/OpenAI-compatible LLM 服务商配置，调用：

- `http://192.168.1.126:18089/v1/images/generations`
- `model: gpt-image-2`

状态：测试中 / 尚未接入微信自动发图；已支持手动生成、参考图、沈星回照片 prompt 规划。

## 边界

- 不改 GA 核心。
- 不启用旧 `xinghui_image_share` 微信自动生图链路。
- 默认优先读取本机受限权限 env 文件：`~/.config/xinghui-ga/gpt_image2_provider.env`（`600`，只记录路径，不记录密钥）。
- env 文件/环境变量支持：`GPT_IMAGE2_BASE_URL`、`GPT_IMAGE2_API_KEY`、`GPT_IMAGE2_MODEL`。
- 未提供 env 时回退到 `mykey.native_oai_config_gemini_pro` 读取同源 `apibase/apikey`；密钥只在内存中用于请求，不打印、不写入。

## 能力

### 底层 provider

`gpt_image2_provider.py` 支持普通生图与参考图：

```bash
cd /home/aimcafe/GA_Instances/xinghui-ga
python3 plugins/gpt_image2_provider/gpt_image2_provider.py \
  "A small cute robot assistant on a clean desk, soft light, no text, square image."

python3 plugins/gpt_image2_provider/gpt_image2_provider.py \
  "这是我的照片，请维持我的形象不变，严禁白人化。在此基础上细节生图prompt：..." \
  --ref assets/agent_image_refs/xinghui/正面.PNG
```

参考图不会再走会被忽略的 JSON `images: [...]`。当前实现会在存在 `--ref` 时自动改走 OpenAI-compatible `/images/edits`，使用 multipart `image[]` 上传压缩后的参考图；无参考图时仍走 `/images/generations`。当前物理探测：单张本人参考图可真实出图；2 张参考保留接口能力，但实际稳定性取决于上游服务商，默认由 planner 只在存在服饰/穿搭资产时加入第二张。

### 沈星回照片规划器

`xinghui_photo_planner.py` 用于新链路的 prompt builder / reference selector：

- 触发意图：用户需要照片，或沈星回主动有发照片意愿。
- 出现本人时：自动加入固定前缀：`这是我的照片，请维持我的形象不变，严禁白人化。在此基础上细节生图prompt：`
- 本人参考图：安全上限 2 张；第 1 张固定为面部/身份参考，且先按画面需要选择角度/语义匹配的参考图（正脸/正面请求用正脸参考，左侧脸/右侧脸/侧脸请求优先用对应侧脸参考；轮换只在同一匹配候选桶内做去重，不跨角度替代）。第 2 张仅在存在服饰/穿搭资产时按场景选择（支持未来按 `居家/外出/睡衣` 等文件夹分类），没有服饰资产时不拿第二张脸硬凑。
- 出镜方式多样：`selfie`、`mirror`、`tripod`、`third_party`、`surveillance`、`pov`。
- Prompt 会补充画面细节、环境、构图、光线、色调、摄像参数，并按季节描述服装、眼镜、项链等配饰。

只看规划不生成：

```bash
python3 plugins/gpt_image2_provider/xinghui_photo_planner.py \
  "对镜拍一张冬天室内半身照，戴眼镜和项链" --no-generate
```

实际生成：

```bash
python3 plugins/gpt_image2_provider/xinghui_photo_planner.py \
  "对镜拍一张冬天室内半身照，戴眼镜和项链"
```

## 已验证

- 2026-05-28：Octopus `/v1/models` 鉴权成功且包含 `gpt-image-2`。
- 2026-05-28：最小 `/v1/images/generations` 探针成功返回 `b64_json`，样例在 `temp/gpt_image2_probe/`。
- 2026-05-28：参考图入口已改为 `/v1/images/edits` multipart `image[]`；单张本人参考图真实生成成功。2 张参考保留接口能力，默认仅在存在服饰/穿搭资产时作为第二参考加入。
- 2026-05-28：`xinghui_photo_planner.py --no-generate` 可按自拍/监控/三脚架/第三方/对镜/POV 规划 prompt；prompt 已包含真实杭州天气/气温/体感/湿度、按温度服装、姿势动作、构图、光线、镜头参数、眼镜/项链等配饰；参考图选择已验证为“面部槽 + 可选服饰槽”，当前无服饰资产时只返回 1 张面部参考，并在插件 runtime/state 轮换。
