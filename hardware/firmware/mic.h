#ifndef MIC_H
#define MIC_H

#include <Arduino.h>
#include <stdint.h>
#include "deskbot_config.h"

#define PDM_MIC_CLK DESKBOT_PDM_MIC_CLK
#define PDM_MIC_DATA DESKBOT_PDM_MIC_DATA

/* 麦克风上行：
 * - setup_mic：I2S0 + OpusEncoder
 * - mic_set_speaker_state：保留播放状态；mic_set_ws_state：控制上行启停
 * - ws_ok：播放/录音同时进行，enhance → send_to_ws(pcm, false)，满 5 帧发送
 * - 不按播放边沿或固定时长 flush；实时模型的服务端 VAD 负责判停 */

static constexpr size_t kMicFrameSamples = 320; /* 20ms @ 16kHz */

struct MicFrame {
  int16_t pcm[kMicFrameSamples];
};

enum MicSpeakerState : int8_t {
  kMicSpeakStart = 0,
  kMicSpeakEnd = 1,
};

enum MicWsState : int8_t {
  kMicWsError = 0,
  kMicWsOk = 1,
};

bool setup_mic();
void task_setup_mic();

void mic_set_speaker_state(MicSpeakerState s);
void mic_set_ws_state(MicWsState s);

/** 本段已成功入 WS TX 的 PCM 样点数（调试/日志）。 */
uint32_t mic_uplink_samples_sent(void);

/** speak_end && ws_ok。 */
bool mic_capture_allowed(void);

void enhance_voice(int16_t* data, size_t length);
void enhance_voice_reset(void);
void mic_set_gain(int gain);
int mic_get_gain(void);

#endif
