#pragma once

#include <stddef.h>
#include <driver/gpio.h>

/* Machine-specific credentials live in this ignored header. Copy
 * deskbot_local_config.example.h to deskbot_local_config.h before building. */
#if __has_include("deskbot_local_config.h")
#include "deskbot_local_config.h"
#endif

/* ========== 本地网络 ==========
 * 真实值只写入被 Git 忽略的 deskbot_local_config.h。
 * 若 SSID 留空 → 热点 Deskbot_Rom，http://192.168.4.1/ 配网；NVS 已存凭证优先。
 * WS host 留空 → 禁用 WebSocket 上行。
 */
#ifndef WIFI_DEFAULT_SSID
#define WIFI_DEFAULT_SSID ""
#endif
#ifndef WIFI_DEFAULT_PASSWORD
#define WIFI_DEFAULT_PASSWORD ""
#endif

#ifndef DESKBOT_WS_HOST
#define DESKBOT_WS_HOST ""
#endif
#ifndef DESKBOT_WS_PORT
#define DESKBOT_WS_PORT 9000
#endif

/* 服务端 WebSocket 鉴权 Key（odk_... 或 odk_free_...）。留空则无法连接 /asr_chat。 */
#ifndef DESKBOT_API_KEY
#define DESKBOT_API_KEY ""
#endif

/* ========== Direct cloud experiment ==========
 * 0: keep the proven ESP32 -> Deskbot server path.
 * 1: ESP32 connects to Doubao Realtime directly; no self-hosted server is used.
 *
 * Credentials are intentionally not committed here.  For a test build pass
 * them as PlatformIO build flags, for example:
 *   -DDESKBOT_DIRECT_CLOUD=1
 *   -DDESKBOT_DOUBAO_APP_ID=\"...\"
 *   -DDESKBOT_DOUBAO_ACCESS_TOKEN=\"...\"
 *   -DDESKBOT_ARK_API_KEY=\"...\"
 */
#ifndef DESKBOT_DIRECT_CLOUD
#define DESKBOT_DIRECT_CLOUD 0
#endif

#ifndef DESKBOT_DOUBAO_HOST
#define DESKBOT_DOUBAO_HOST "openspeech.bytedance.com"
#endif
#ifndef DESKBOT_DOUBAO_PORT
#define DESKBOT_DOUBAO_PORT 443
#endif
#ifndef DESKBOT_DOUBAO_PATH
#define DESKBOT_DOUBAO_PATH "/api/v3/duplex/realtime/dialogue"
#endif
#ifndef DESKBOT_DOUBAO_APP_ID
#define DESKBOT_DOUBAO_APP_ID ""
#endif
#ifndef DESKBOT_DOUBAO_ACCESS_TOKEN
#define DESKBOT_DOUBAO_ACCESS_TOKEN ""
#endif
#ifndef DESKBOT_DOUBAO_RESOURCE_ID
#define DESKBOT_DOUBAO_RESOURCE_ID "volc.speech.dialog"
#endif
/* This is the protocol App-Key used by the legacy Realtime endpoint, not the
 * account Secret Key.  The Secret Key is never placed on the robot. */
#ifndef DESKBOT_DOUBAO_PROTOCOL_APP_KEY
#define DESKBOT_DOUBAO_PROTOCOL_APP_KEY "PlgvMymc7f3tQnJ6"
#endif
#ifndef DESKBOT_DOUBAO_MODEL
#define DESKBOT_DOUBAO_MODEL "1.2.6.1"
#endif
#ifndef DESKBOT_DOUBAO_VOICE
#define DESKBOT_DOUBAO_VOICE "zh_female_xiaohe_jupiter_bigtts"
#endif

/* Seed VLM is exposed to Realtime as an endpoint-side camera tool. */
#ifndef DESKBOT_ARK_API_KEY
#define DESKBOT_ARK_API_KEY ""
#endif
#ifndef DESKBOT_ARK_RESPONSES_URL
#define DESKBOT_ARK_RESPONSES_URL "https://ark.cn-beijing.volces.com/api/v3/responses"
#endif
#ifndef DESKBOT_ARK_VISION_MODEL
#define DESKBOT_ARK_VISION_MODEL "doubao-seed-2-1-turbo-260628"
#endif
#ifndef DESKBOT_DIRECT_VISION_TIMEOUT_MS
#define DESKBOT_DIRECT_VISION_TIMEOUT_MS 20000
#endif

/** Initial direct-mode test uses encrypted WSS without CA verification.
 * Production firmware must pin/install a CA certificate before setting this
 * to 0. */
#ifndef DESKBOT_DIRECT_TLS_INSECURE
#define DESKBOT_DIRECT_TLS_INSECURE 1
#endif

/** Until ESP-SR AFE/AEC is integrated, preserve the realtime media clock but
 * replace microphone samples with silence while the speaker is audible. */
#ifndef DESKBOT_DIRECT_ECHO_SUPPRESS
#define DESKBOT_DIRECT_ECHO_SUPPRESS 1
#endif

#ifndef DESKBOT_DIRECT_AUDIO_PREBUFFER_MS
#define DESKBOT_DIRECT_AUDIO_PREBUFFER_MS 600
#endif

#define ASR_CHAT_HOST DESKBOT_WS_HOST
#define ASR_CHAT_PORT DESKBOT_WS_PORT

/** 相机 JPEG 独立 WebSocket 路径（与 /asr_chat 分离）。 */
#ifndef DESKBOT_CAMERA_WS_PATH
#define DESKBOT_CAMERA_WS_PATH "/camera_uplink"
#endif

/** 1 = 经独立 /camera_uplink 上传（camera_frame JSON + JPEG binary）；0 = 完全停用相机 WS。 */
#ifndef DESKBOT_CAMERA_UPLINK_ENABLED
#define DESKBOT_CAMERA_UPLINK_ENABLED 1
#endif

static inline bool deskbot_camera_uplink_enabled(void) {
  return DESKBOT_CAMERA_UPLINK_ENABLED != 0;
}

static inline bool deskbot_ws_configured(void) {
  return DESKBOT_WS_HOST[0] != '\0';
}

static inline bool deskbot_api_key_configured(void) {
  return DESKBOT_API_KEY[0] != '\0';
}

/* ========== 硬件接线（Seeed XIAO ESP32S3 Sense）==========
 * 焊盘: D0=1 D1=2 D2=3 D3=4 D4=5 D5=6 D6=43 D7=44 D8=7 D9=8 D10=9
 * 图纸 IO8/IO3 = GPIO 编号，非丝印 D8/D3。
 * 显示屏：微雪 1.83" ST7789P 240×284，RST/BL 接 3.3V（不经 MCU GPIO）
 */

#define DESKBOT_DISPLAY_MOSI 9
#define DESKBOT_DISPLAY_SCK  7
#define DESKBOT_DISPLAY_CS   2
#define DESKBOT_DISPLAY_DC   3

#define DESKBOT_DISPLAY_WIDTH 240
#ifndef DESKBOT_DISPLAY_HEIGHT
#define DESKBOT_DISPLAY_HEIGHT 284
#endif
#ifndef DESKBOT_DISPLAY_ROW_OFFSET
#define DESKBOT_DISPLAY_ROW_OFFSET 36
#endif
#ifndef DESKBOT_DISPLAY_COL_OFFSET
#define DESKBOT_DISPLAY_COL_OFFSET 0
#endif

#ifndef DESKBOT_DISPLAY_TOP_SAFE_PX
#define DESKBOT_DISPLAY_TOP_SAFE_PX 4
#endif

#define DESKBOT_PB_COORD_W DESKBOT_DISPLAY_HEIGHT
#define DESKBOT_PB_COORD_H 240
#ifndef DESKBOT_DISPLAY_CANVAS_X0
#define DESKBOT_DISPLAY_CANVAS_X0 ((DESKBOT_DISPLAY_HEIGHT - DESKBOT_PB_COORD_W) / 2)
#endif
#ifndef DESKBOT_DISPLAY_ROT3_XSTART_ADJ
#define DESKBOT_DISPLAY_ROT3_XSTART_ADJ (-18)
#endif

#define DESKBOT_DRAW_W DESKBOT_PB_COORD_W
#define DESKBOT_DRAW_H DESKBOT_PB_COORD_H

/* 舵机 PWM（已避开 UART0 的 D6/D7）
 * 左右(X) → D9/GPIO8 小舵机；上下(Y) → D3/GPIO4 大舵机 */
#ifndef DESKBOT_ROM_X_PIN
#define DESKBOT_ROM_X_PIN 8
#endif
#ifndef DESKBOT_ROM_Y_PIN
#define DESKBOT_ROM_Y_PIN 4
#endif

#ifndef DESKBOT_AUDIO_PLAY_VOLUME
#define DESKBOT_AUDIO_PLAY_VOLUME 1.0f
#endif

#define DESKBOT_ROM_MAX98357_DIN  GPIO_NUM_1
#define DESKBOT_ROM_MAX98357_BCLK GPIO_NUM_6
#define DESKBOT_ROM_MAX98357_LRC  GPIO_NUM_5
#define DESKBOT_ROM_MAX98357_SD   GPIO_NUM_NC
#define DESKBOT_ROM_MAX98357_GAIN GPIO_NUM_NC

#define DESKBOT_PDM_MIC_CLK  GPIO_NUM_42
#define DESKBOT_PDM_MIC_DATA GPIO_NUM_41

/* 能量门控（enhance_voice 后本地预处理，切句在服务端 Silero VAD） */
#define DESKBOT_PDM_VOICE_MARGIN             320
#define DESKBOT_PDM_VOICE_HANGOVER_MARGIN    200
#define DESKBOT_PDM_VOICE_TRIGGER_RATIO_NUM    130
#define DESKBOT_PDM_VOICE_TRIGGER_RATIO_DEN  100
/** 触发阈值绝对下限（enhance_voice×5 后的 mean-abs）；防安静环境下 thr 过低。 */
#define DESKBOT_PDM_VOICE_TRIGGER_FLOOR      140

static inline size_t deskbot_pdm_voice_trigger_thr(size_t ema) {
  const size_t t_delta = ema + (size_t)DESKBOT_PDM_VOICE_MARGIN;
  const size_t t_ratio =
      (ema * (size_t)DESKBOT_PDM_VOICE_TRIGGER_RATIO_NUM) / (size_t)DESKBOT_PDM_VOICE_TRIGGER_RATIO_DEN;
  /* 取较高者：旧 min() 在 ema≈60 时 thr≈63，3m 人声也会触发。 */
  size_t thr = (t_delta > t_ratio) ? t_delta : t_ratio;
  if (thr < (size_t)DESKBOT_PDM_VOICE_TRIGGER_FLOOR) {
    thr = (size_t)DESKBOT_PDM_VOICE_TRIGGER_FLOOR;
  }
  return thr;
}

static inline size_t deskbot_pdm_voice_hangover_thr(size_t ema) {
  return ema + (size_t)DESKBOT_PDM_VOICE_HANGOVER_MARGIN;
}
#define DESKBOT_PDM_EMA_QUIET_RATIO_NUM      102
#define DESKBOT_PDM_EMA_QUIET_RATIO_DEN      100
/** 连续超阈帧数（20ms/帧）；3=60ms，可滤远场短促人声。 */
#define DESKBOT_PDM_VOICE_TRIGGER_FRAMES     3
#define DESKBOT_PDM_VOICE_THRESHOLD_MAX      24000
#define DESKBOT_PDM_PRE_VOICE_FRAMES         50
/** 说完后连续静音多久结束本轮（ms）；600–700 适合短指令，句内长停顿需靠 hangover 续录。 */
#define DESKBOT_PDM_SILENCE_END_MS           650

/** I2S 播放 chunk 的 mean-abs×volume 低于此值视为静音，isSpeaking 保持 false。 */
#define DESKBOT_SPEAKER_AUDIBLE_MEAN_ABS     16

/** TTS 结束后尾音抑制（ms）；无 AEC 时开麦前丢弃环内回声。 */
#ifndef DESKBOT_TAIL_SUPPRESS_MS
#define DESKBOT_TAIL_SUPPRESS_MS               300
#endif

/** 相机 JPEG 上行最小间隔（ms）；持续上传，不因听音/播音降频或暂停。 */
#ifndef DESKBOT_CAMERA_UPLINK_INTERVAL_MS
#define DESKBOT_CAMERA_UPLINK_INTERVAL_MS      500
#endif

/** 设备状态（舵机角/音量等）上行最小间隔（ms）；仅对 go 通知生效，go_now 立即发。 */
#ifndef DESKBOT_STATE_UPLINK_INTERVAL_MS
#define DESKBOT_STATE_UPLINK_INTERVAL_MS      10000
#endif

/** 单轮连续 Opus 上行上限（秒）；正常由 pb_start 提前结束。 */
#ifndef DESKBOT_UPLINK_MAX_SEC
#define DESKBOT_UPLINK_MAX_SEC                 30
#endif

/** WS TCP 握手 + upgrade 等待上限（ms）；重连时适当加长。 */
#ifndef DESKBOT_WS_CONNECT_TIMEOUT_MS
#define DESKBOT_WS_CONNECT_TIMEOUT_MS          10000
#endif

/** disconnect 后泵 loop 清空 lwIP 发送队列（ms）。 */
#ifndef DESKBOT_WS_DISCONNECT_DRAIN_MS
#define DESKBOT_WS_DISCONNECT_DRAIN_MS         1500
#endif

