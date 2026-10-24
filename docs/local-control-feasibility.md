# D4sh 本地/LAN 管理控制可行性调研

日期：2026-10-24

## 结论摘要

本次仅做静态逆向和只读环境检查，没有向真实设备发送喂食、计划或锁操作。

| 能力 | 结论 | 证据强度 |
| --- | --- | --- |
| D4sh 手动/临时喂食 | 已检查的 App 调用路径只有云端 HTTP `d4sh/saveDailyFeed` | 已验证（静态调用链） |
| D4sh 喂食计划读取与保存 | 已检查的 App 调用路径使用云端 HTTP `d4sh/feed`、`d4sh/saveFeed` | 已验证（静态调用链） |
| D4sh 设备状态刷新 | 已检查的 App 调用路径使用云端 HTTP `d4sh/devicestate` | 已验证（静态调用链） |
| D4sh `network/lock` | 存在云端 HTTP `d4sh/network/lock`，在设备绑定的合法性校验流程调用 | 已验证（静态调用链）；不是已验证的喂食器物理锁 |
| D4sh 本地管理协议 | 未发现 | 未确认；不能据是否存在 `ip` 字段或 UDP 库推断 |
| D4sh XP2P/RTC | 已检查的实现用于实时视频、音频/对讲、云台、画质、截图和录制 | 已验证（静态调用链） |
| RTC LAN 加速只作用于媒体 | 在 D4sh 已检查路径中，`rtc.lan_accelerate_enable` 仅设置在两个 RTC 媒体服务中 | 已验证（静态）；不足以证明任意管理能力可走 LAN |
| 当前环境真实 LAN 命中 | 未验证 | 无已连接 Android 设备、无 APK 文件、无真实设备会话 |

因此，**没有协议级证据支持实现 D4sh 本地喂食或本地控制**。现有证据支持把该方向标记为不可实施，而不是“已有 IP 字段所以可能可行”。

### 合理推断

- D4sh UI 把喂食、计划和状态作为云端管理请求提交，设备命令很可能由云端服务转发；但转发协议、离线队列和鉴权细节没有在 App 包内得到证据。
- `d4sh/network/lock` 很可能用于绑定过程中的网络/设备合法性校验，因为它的唯一明确调用点是绑定流程；具体防止什么冲突或滥用仍是未知。
- `D4shRecord.ip` 很可能来自设备配网后上报的网络信息，用于绑定完整性检查；没有证据表明它被当作 D4sh 管理连接地址。
- RTC LAN 加速开关很可能改善手机、摄像机和侧车之间的媒体路径；实际是否命中 LAN 必须由 RTC 统计或抓包确认。

## 资料与可复现步骤

### App 身份

静态资料目录：

```text
/tmp/petkit-jadx-full.h7xwmo
```

该导出包含 JADX 源码，但没有原始 APK、`AndroidManifest.xml` 或非空 resources 目录。App 身份取自源码中的 `BuildConfig.java`：

```bash
sed -n '1,30p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/BuildConfig.java
```

- Package：`com.petkit.android`
- Version name：`14.1.0`
- Version code：`14001000`

限制：原始 APK 的路径和 SHA-256 不在当前资料中，因此不能核对导出版本与某个 APK 二进制是否一致。App 没有在模拟器中启动；`/tmp/petkit-jadx-full.h7xwmo/resources` 为空。

### 云端 API 与基址

端点定义：

```bash
rg -n 'SAMPLE_API_D4SH_' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/api/http/ApiTools.java
```

生产基址和 URL 拼接：

```bash
sed -n '1120,1130p;1325,1355p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/api/http/ApiTools.java
sed -n '1,150p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/api/http/AsyncHttpUtil.java
```

已验证事实：

- 生产主机为 `api.petkit.cn`。
- `ApiTools.getApiHTTPSUri()` 返回 `https://api.petkit.cn/6/`。
- `AsyncHttpUtil.getAbsoluteUrl()` 对相对路径追加上述基址。
- `D4shService` 的所有管理端点均有 `@Headers({"Domain-Name: petkit"})`。

### 手动/临时喂食

源码：

```bash
sed -n '220,285p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh/widget/D4shManualWindow.java
```

已验证调用链：

1. `D4shHomeActivity.showManualFeedWindow()` 创建 `D4shManualWindow`。
2. `D4shManualWindow.saveManualFeed()` 构造 `deviceId`、`day`、`time`、`amount1`、`amount2`。
3. 通过 `AsyncHttpUtil.post(..., SAMPLE_API_D4SH_SAVE_DAILY_FEED, ...)` 发出 HTTP POST。
4. 常量为 `d4sh/saveDailyFeed`，最终 URL 为：

```text
https://api.petkit.cn/6/d4sh/saveDailyFeed
```

`time == -1` 表示“现在”；即使即时喂食仍走同一个云端 POST。停止当前喂食由
`D4shHomePresenter.stopFeeding()` 调用 `D4shService.cancelRealTimeFeed()`，
端点为 `d4sh/cancelRealtimeFeed`。

复现：

```bash
rg -n 'saveManualFeed|SAVE_DAILY_FEED|stopFeeding|cancelRealTimeFeed' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh
```

结论：在已检查的 App 路径中，手动/临时喂食与停止喂食只有云端 HTTP 调用，没有设备私网协议调用。

### 喂食计划

端点与模型：

```bash
sed -n '110,155p;260,305p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/api/service/D4shService.java
sed -n '105,145p;175,215p;240,280p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/device/presenter/DeviceFeedDifferentPlans2Presenter.java
sed -n '35,100p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/device/model/DeviceFeedDifferentPlans2Model.java
```

已验证事实：

- `DeviceFeedDifferentPlans2Presenter` 对 device type `25`（D4sh）调用
  `getD4shDifferentPlan()`。
- `DeviceFeedDifferentPlans2Model` 将对应该请求交给 `D4shService.getD4shDifferentPlan()`。
- 该端点映射到 `d4sh/feed`。
- 保存 D4sh 计划时调用 `saveD4shFeed()`，该端点映射到 `d4sh/saveFeed`。
- 其他计划复制、切换流程复用相同 `D4shService.saveD4shFeed()`。

结论：在已检查的计划读取、保存、切换和复制路径中，没有本地设备协议。

### 状态刷新

调用链：

```bash
sed -n '385,430p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh/presenter/D4shHomePresenter.java
sed -n '70,90p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh/model/D4shHomeModel.java
sed -n '130,145p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/api/service/D4shService.java
```

已验证调用链：

```text
D4shHomePresenter.updateDeviceState()
  -> D4shHomeModel.updateDeviceState()
  -> D4shService.getD4shDeviceState()
  -> POST d4sh/devicestate
```

同一 Presenter 还从 `d4sh/feed` 读取计划，从 `d4sh/device_detail` 读取设备详情。`D4shRecord` 是本地 SugarRecord 缓存，但缓存来源和刷新动作在已检查路径中是云端响应，不能据此推导本地控制通道。

### “锁”的语义

```bash
sed -n '310,330p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/api/service/D4shService.java
sed -n '925,970p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/utils/BleDeviceBindUtils.java
```

已验证事实：

- D4sh 只有 `d4sh/network/lock` 这一个名称含 `lock` 的端点。
- `D4shService.verifyLegalityD4SH()` 调用该端点。
- device type `25` 的调用方是 `BleDeviceBindUtils` 的绑定合法性校验分支。
- 返回值只会触发 `verifyLegalitySuccess()` 或 `verifyLegalityFail()`。
- 没有找到把该端点解释为喂食器物理锁的业务字段或 UI 调用。

结论：

- 若“锁”指绑定/网络合法性锁，则已确认它走云端。
- 若“锁”指喂食器物理锁，则当前资料没有协议级证据；不能确认 App 是否支持，也不能据此实现。

### `ip` 字段和无线路由状态

```bash
sed -n '50,65p;215,255p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh/mode/D4shRecord.java
rg -n --glob '*.java' 'getIp\\(\\)' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/media \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/home
```

已验证事实：

- `D4shRecord` 实现通用 `DeviceInfos`，其中包含 `ip`、BSSID、SSID 和 RSSI。
- D4sh 绑定进度通过 BLE 消息收到这些网络信息。
- `BleDeviceBindUtils.checkDeviceNetInfo()` 只检查 IP/BSSID/SSID/RSSI 是否为空，作为绑定信息完整性条件。
- D4sh 管理路径没有把 `D4shRecord.getIp()` 作为 socket 目标来建立喂食、计划、状态或锁连接。

这不能证明设备不允许本地控制。它只证明当前静态搜索没有找到这样的调用；反射、动态加载、native 代码和服务端开关仍未覆盖。

### XP2P/RTC 的用途

服务选择器：

```bash
sed -n '1,90p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh/utils/D4shLiveServiceSelector.java
sed -n '260,300p' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android/activities/petkitBleDevice/d4sh/presenter/D4shHomePresenter.java
```

已验证事实：

- `D4shLiveServiceSelector` 只在 D4sh Home、Personalized Dressing 等实时媒体页面选择
  `RTC`、`TENCENT`（XP2P）或 `AGORA`。
- XP2P 的显式能力包括实时 FLV URL、声音播放、对讲采集/发送、云台移动、画质切换、截图和录制。
- RTC 的显式能力包括加入频道、渲染视频、音视频订阅、对讲、云台消息、画质、录制和 `start_live` 媒体会话。
- 这些媒体服务没有进入手动喂食、计划、状态或网络锁的调用链。

结论：在已检查路径中，XP2P/RTC 是媒体通道；其中包含对讲和云台这类媒体控制，不等于喂食器管理控制。

### RTC LAN 加速参数

```bash
rg -n --glob '*.java' 'lan_accelerate_enable|lanAccelerateState' \
  /tmp/petkit-jadx-full.h7xwmo/sources/com/petkit/android
```

已验证事实：

- `RtcService` 与 `AgoraLiveService` 在加入媒体频道前调用
  `setParameters("{\"rtc.lan_accelerate_enable\": true}")`。
- 两个服务的 `onRtcStats` 会记录 `lanAccelerateState`。
- 搜索到的两个调用点都位于媒体服务。

这证明官方 App 请求 RTC LAN 加速，也证明该开关在已观察代码中局限于媒体链路。它不能证明喂食、计划、状态或锁有 LAN 路径。

## 侧车改动与 LAN 命中边界

侧车在创建 RTC connection 后、加密和加入频道前设置：

```text
{"rtc.lan_accelerate_enable": true}
```

参数设置函数和回归测试位于：

```text
media_sidecar/internal/agora/factory.go
media_sidecar/internal/agora/factory_test.go
```

已验证：

- 两个 RTC 连接参数按顺序发送：payload type 69、LAN acceleration。
- 现有加密、音视频双流、订阅和会话生命周期代码路径未改变。
- 构建和 Go 测试通过。

构建与测试复现：

```bash
cd media_sidecar
./scripts/build-local.sh

cd .build/agora-go-sdk
CGO_ENABLED=1 go test -count=1 ./petkit_media/...
```

本次结果：

- 构建产物：`media_sidecar/.build/petkit-media`，Mach-O 64-bit arm64。
- `internal/agora`、`internal/hls`、`internal/server` 测试均通过。
- Agora macOS SDK 的 linker 报告目标 macOS 版本高于本机链接目标；这是环境警告，构建和测试仍以 0 退出。
- 固定 Agora Go SDK 版本：`v2.4.22`，commit
  `6be0fd16cabe822db3aecd984e90d2e38b2d1aaa`。

未验证：

- RTC 是否实际选择 LAN 路径。
- 摄像机和侧车是否在同一 LAN。
- 具体链路的端点、端口和媒体字节是否经过私网。

当前主机有 AVD `Pixel_6_Pro_API_33` 和 `Pixel_API_33`，但 `adb devices -l` 没有已连接设备；在 `/tmp` 和 Downloads 中没有找到 APK。因此没有启动 App、没有登录、没有真实设备会话，也没有抓包。

## 复现 LAN 命中测量

只有同时具备真实 D4sh、与侧车同一 LAN、有效 PETKIT 会话和主机抓包权限时，下述测量才有意义。命令中不要保存或展示账号、密码、Token、设备 IP、序列号或完整媒体 URL。

1. 启动侧车并建立一次真实会话。不要记录 `POST /v1/sessions` 的请求体。

2. 检查进程是否存在私网 UDP/TCP 连接：

```bash
sidecar_pid=$(pgrep -x petkit-media)
lsof -nP -a -p "$sidecar_pid" -i
```

只看地址类别、端口和协议；输出中的真实 IP 不应进入报告或提交。

3. 在主机网卡上抓包，使用临时文件和非登录抓包：

```bash
sudo tcpdump -i en0 -n -s 0 -w /tmp/petkit-rtc-lan.pcap 'udp or tcp'
```

4. 用 TShark 做端点摘要，原始 pcap 留在 `/tmp`，不提交：

```bash
tshark -r /tmp/petkit-rtc-lan.pcap -q -z endpoints,ip
```

5. 判定标准：

- 若出现设备私网地址与侧车之间的持续 RTC 媒体流量，才可标记“观察到 LAN 媒体命中”。
- 仅有 Agora 公网端点、仅有配置成功、仅有 `lanAccelerateState` 字段或仅有本地 `ip` 字段，都不算 LAN 命中。
- 若没有真实设备或抓包条件，必须保留“未验证”。

Android 模拟器不是这一项的替代证据：模拟器网络经过 NAT，且仍需真实摄像机和 PETKIT 会话；当前也没有可安装的 APK。

## 风险与未知

1. 本次是 JADX 静态分析，未运行 APK，未观察真实请求或响应；服务端行为、feature flag、反射、动态加载和 native 代码仍是未知。
2. JADX 输出的部分方法可能反编译不完整；报告只对明确调用链作“已验证”判断。
3. `Domain-Name: petkit` 与生产基址支持云端结论，但 App 可能存在主机切换；未做运行时 DNS/HAR 验证。
4. 没有发现 D4sh 管理侧本地 socket 协议，不等于协议在数学上不存在；要推翻当前结论，需要可复现的私网报文、协议字段和响应语义。
5. RTC LAN 加速可能让部分媒体流量走私网；已检查的管理调用链仍全部指向云端。若要改走设备私网，需要新的协议和鉴权证据。
6. 直接构造云端请求或尝试设备私网报文均会越过本次只读调研边界；本次没有执行，也不应把猜测性命令作为后续实现依据。

## 后续取证建议

1. 提供原始 APK 路径和 SHA-256，补全 manifest、native library 和版本一致性证据。
2. 使用测试账号和真实 D4sh，在同 LAN 下分别执行刷新、手动喂食和计划读取；仅抓取元数据，不提交敏感载荷。
3. 同时采集 Android `lanAccelerateState`、侧车 RTC 日志和主机 pcap，分别回答“媒体是否命中 LAN”和“管理请求是否访问私网”。
4. 若未来发现私网管理报文，先记录端点、传输层、鉴权、重放保护、幂等性、失败恢复和设备型号/固件边界；在这些证据齐备前不要实现写操作。

## 本次未执行的操作

- 未修改主 worktree `/Users/lionoggo/Workspaces/Petkit`。
- 未创建其他 worktree。
- 未提交、推送或创建 MR/PR。
- 未向真实设备喂食、修改计划、停止喂食、加锁/解锁或发送私网控制报文。
- 未登录宠物 App，未导出账号、密码、Token、设备 IP 或序列号。
