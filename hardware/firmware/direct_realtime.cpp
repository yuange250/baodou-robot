#include "direct_realtime.h"

#include "camera.h"
#include "deskbot_config.h"
#include "display.h"
#include "head.h"
#include "logger.h"
#include "mic.h"
#include "speaker.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <atomic>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <mbedtls/base64.h>
#include <string.h>

namespace {

constexpr uint32_t kInputSampleRate = 16000;
constexpr uint32_t kOutputSampleRate = 24000;
constexpr size_t kMicFrameSamples = 320;       // 20 ms
constexpr size_t kMicBatchFrames = 5;          // one WSS write per 100 ms
constexpr size_t kMicBatchSamples = kMicFrameSamples * kMicBatchFrames;
constexpr size_t kMicPoolSize = 12;             // 1.2 s uplink cushion
constexpr size_t kDownQueueDepth = 96;
constexpr size_t kAudioJsonCapacity = 4608;
constexpr uint32_t kSessionReadyTimeoutMs = 15000;
constexpr uint32_t kIdleFirstDelayMs = 5000;
constexpr uint32_t kIdleIntervalMinMs = 4000;
constexpr uint32_t kIdleIntervalJitterMs = 5000;
constexpr uint32_t kMouthRenderIntervalMs = 75;
/*
 * Both Realtime WSS and the on-demand Ark request need their own mbedTLS
 * context.  Arduino's prebuilt ESP32 core forces those TLS buffers into
 * internal SRAM, so oversized task stacks can make the second handshake fail
 * even though several megabytes of PSRAM remain free.
 */
constexpr uint32_t kTaskStack = 24 * 1024;
constexpr UBaseType_t kTaskPriority = 5;
constexpr uint32_t kVisionTaskStack = 10 * 1024;
constexpr size_t kVisionQuestionMax = 384;
constexpr size_t kVisionAnswerMax = 1000;

struct MicBatch {
  size_t count = 0;
  int16_t pcm[kMicBatchSamples];
};

struct DownChunk {
  int16_t* pcm = nullptr;
  size_t samples = 0;
};

struct PendingCall {
  char call_id[72]{};
  char name[40]{};
};

struct VisionJob {
  char call_id[72]{};
  char question[kVisionQuestionMax]{};
  uint32_t preamble_sent_ms = 0;
  bool wait_for_preamble = false;
};

struct VisionResult {
  char call_id[72]{};
  char* output = nullptr;
  char* spoken = nullptr;
};

WebSocketsClient s_ws;
TaskHandle_t s_task = nullptr;
TaskHandle_t s_vision_task = nullptr;
QueueHandle_t s_mic_free_q = nullptr;
QueueHandle_t s_mic_ready_q = nullptr;
QueueHandle_t s_down_q = nullptr;
QueueHandle_t s_vision_job_q = nullptr;
QueueHandle_t s_vision_result_q = nullptr;
MicBatch* s_mic_pool = nullptr;
MicBatch* s_mic_build = nullptr;  // mic task is the sole owner
char* s_audio_json = nullptr;

std::atomic<bool> s_ready{false};
std::atomic<bool> s_link_down{false};
std::atomic<bool> s_reconnect_requested{false};
/*
 * The Arduino ESP32 core keeps mbedTLS buffers in internal SRAM.  Two live TLS
 * clients (Realtime WSS + Ark HTTPS) do not fit reliably, so the owner task
 * lends the TLS budget to the vision worker and reconnects afterwards.
 */
std::atomic<bool> s_vision_tls_requested{false};
std::atomic<bool> s_vision_tls_granted{false};
bool s_setup_ok = false;
bool s_ws_started = false;
bool s_tcp_connected = false;
bool s_session_create_pending = false;
unsigned long s_session_create_sent_ms = 0;
unsigned long s_last_connect_attempt_ms = 0;
uint32_t s_event_seq = 0;
uint32_t s_mic_drop_count = 0;
uint32_t s_down_drop_count = 0;

bool s_response_active = false;
bool s_response_done = false;
bool s_play_started = false;
bool s_play_end_enqueued = false;
size_t s_down_buffered_samples = 0;
bool s_input_active = false;
bool s_idle_variant_active = false;
unsigned long s_idle_deadline_ms = 0;
unsigned long s_idle_restore_ms = 0;
unsigned long s_last_activity_ms = 0;
unsigned long s_last_mouth_render_ms = 0;
uint32_t s_mouth_level_sequence = 0;
uint32_t s_mouth_smoothed = 0;
char s_expression[20] = "idle";
char s_last_vision_context[kVisionAnswerMax + 1]{};
PendingCall s_pending_calls[4]{};

static const char kFaceIdle[] =
    R"json([{"ms":400,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":147,"y":153,"w":60,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":152,"y":159,"w":50,"h":3,"radius":1,"c":0}],"extra":[]}}])json";
static const char kFaceIdleBlink[] =
    R"json([{"ms":650,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}],"mouth":[{"shape":"round_rect","x":147,"y":153,"w":60,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":152,"y":159,"w":50,"h":3,"radius":1,"c":0}]}},{"ms":90,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":100,"rw":11,"rh":3,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":100,"rw":11,"rh":3,"c":2047}]}},{"ms":180,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}]}}])json";
static const char kFaceIdleLookAround[] =
    R"json([{"ms":300,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}],"mouth":[{"shape":"round_rect","x":147,"y":153,"w":60,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":152,"y":159,"w":50,"h":3,"radius":1,"c":0}],"extra":[]}},{"ms":420,"elements":{"eye_l":[{"shape":"ellipse_fill","x":96,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":172,"y":97,"rw":11,"rh":11,"c":2047}]}},{"ms":420,"elements":{"eye_l":[{"shape":"ellipse_fill","x":114,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":190,"y":97,"rw":11,"rh":11,"c":2047}]}},{"ms":260,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}]}}])json";
static const char kFaceIdleDrowsy[] =
    R"json([{"ms":320,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":99,"rw":11,"rh":6,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":99,"rw":11,"rh":6,"c":2047}],"mouth":[{"shape":"round_rect","x":151,"y":156,"w":52,"h":14,"radius":7,"c":2047},{"shape":"round_rect","x":156,"y":161,"w":42,"h":2,"radius":1,"c":0}],"extra":[]}},{"ms":520,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":100,"rw":11,"rh":3,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":100,"rw":11,"rh":3,"c":2047}]}},{"ms":300,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":99,"rw":11,"rh":6,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":99,"rw":11,"rh":6,"c":2047}]}}])json";
static const char kFaceIdleYawn[] =
    R"json([{"ms":280,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":100,"rw":11,"rh":4,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":100,"rw":11,"rh":4,"c":2047}],"mouth":[{"shape":"round_rect","x":157,"y":151,"w":40,"h":24,"radius":12,"c":2047},{"shape":"round_rect","x":162,"y":157,"w":30,"h":9,"radius":4,"c":0}],"extra":[]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":157,"y":139,"w":40,"h":48,"radius":20,"c":2047},{"shape":"round_rect","x":162,"y":146,"w":30,"h":32,"radius":15,"c":0}]}},{"ms":300,"elements":{"mouth":[{"shape":"round_rect","x":151,"y":156,"w":52,"h":14,"radius":7,"c":2047},{"shape":"round_rect","x":156,"y":161,"w":42,"h":2,"radius":1,"c":0}]}}])json";
static const char kFaceIdleSnore[] =
    R"json([{"ms":650,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":100,"rw":12,"rh":3,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":100,"rw":12,"rh":3,"c":2047}],"mouth":[{"shape":"round_rect","x":159,"y":153,"w":36,"h":24,"radius":12,"c":2047},{"shape":"round_rect","x":164,"y":159,"w":26,"h":9,"radius":4,"c":0}],"extra":[{"shape":"text","x":221,"y":82,"text":"z","size":2,"c":2047}]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":162,"y":150,"w":30,"h":30,"radius":15,"c":2047},{"shape":"round_rect","x":167,"y":156,"w":20,"h":15,"radius":7,"c":0}],"extra":[{"shape":"text","x":229,"y":67,"text":"Z","size":2,"c":2047}]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":159,"y":153,"w":36,"h":24,"radius":12,"c":2047},{"shape":"round_rect","x":164,"y":159,"w":26,"h":9,"radius":4,"c":0}],"extra":[{"shape":"text","x":221,"y":82,"text":"z","size":2,"c":2047}]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":162,"y":150,"w":30,"h":30,"radius":15,"c":2047},{"shape":"round_rect","x":167,"y":156,"w":20,"h":15,"radius":7,"c":0}],"extra":[{"shape":"text","x":229,"y":67,"text":"Z","size":2,"c":2047}]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":159,"y":153,"w":36,"h":24,"radius":12,"c":2047},{"shape":"round_rect","x":164,"y":159,"w":26,"h":9,"radius":4,"c":0}],"extra":[{"shape":"text","x":221,"y":82,"text":"z","size":2,"c":2047}]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":162,"y":150,"w":30,"h":30,"radius":15,"c":2047},{"shape":"round_rect","x":167,"y":156,"w":20,"h":15,"radius":7,"c":0}],"extra":[{"shape":"text","x":229,"y":67,"text":"Z","size":2,"c":2047}]}},{"ms":650,"elements":{"mouth":[{"shape":"round_rect","x":159,"y":153,"w":36,"h":24,"radius":12,"c":2047},{"shape":"round_rect","x":164,"y":159,"w":26,"h":9,"radius":4,"c":0}],"extra":[{"shape":"text","x":221,"y":82,"text":"z","size":2,"c":2047}]}},{"ms":500,"elements":{"extra":[]}}])json";
static const char kFaceHappy[] =
    R"json([{"ms":800,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":99,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":99,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":141,"y":148,"w":72,"h":28,"radius":14,"c":2047},{"shape":"round_rect","x":146,"y":154,"w":62,"h":13,"radius":6,"c":0}],"extra":[{"shape":"ellipse_fill","x":55,"y":131,"rw":12,"rh":6,"c":64064},{"shape":"ellipse_fill","x":218,"y":131,"rw":12,"rh":6,"c":64064}]}}])json";
static const char kFaceShy[] =
    R"json([{"ms":800,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":161,"y":153,"w":32,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":166,"y":159,"w":22,"h":3,"radius":1,"c":0}],"extra":[{"shape":"ellipse_fill","x":62,"y":133,"rw":9,"rh":4,"c":63488},{"shape":"ellipse_fill","x":222,"y":133,"rw":9,"rh":4,"c":63488}]}}])json";
static const char kFaceAngry[] =
    R"json([{"ms":1000,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":99,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":99,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":145,"y":157,"w":64,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":150,"y":163,"w":54,"h":3,"radius":1,"c":0}],"extra":[{"shape":"line","x1":79,"y1":83,"x2":129,"y2":93,"c":2047},{"shape":"line","x1":79,"y1":86,"x2":129,"y2":96,"c":2047},{"shape":"line","x1":207,"y1":83,"x2":157,"y2":93,"c":2047},{"shape":"line","x1":207,"y1":86,"x2":157,"y2":96,"c":2047}]}}])json";
static const char kFaceSurprised[] =
    R"json([{"ms":800,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":15,"rh":15,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":15,"rh":15,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":163,"y":146,"w":28,"h":32,"radius":16,"c":2047},{"shape":"round_rect","x":168,"y":152,"w":18,"h":17,"radius":8,"c":0}],"extra":[]}}])json";
static const char kFaceSad[] =
    R"json([{"ms":500,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":100,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":100,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":155,"y":157,"w":44,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":160,"y":163,"w":34,"h":3,"radius":1,"c":0}],"extra":[{"shape":"circle","x":107,"y":120,"r":2,"c":65535},{"shape":"circle","x":199,"y":120,"r":2,"c":65535}]}}])json";
static const char kFaceSleep[] =
    R"json([{"ms":800,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":98,"rw":8,"rh":3,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":98,"rw":8,"rh":3,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":149,"y":156,"w":52,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":154,"y":162,"w":42,"h":3,"radius":1,"c":0}],"extra":[]}}])json";
static const char kFaceThinking[] =
    R"json([{"ms":600,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":155,"y":156,"w":44,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":160,"y":162,"w":34,"h":3,"radius":1,"c":0}],"extra":[{"shape":"line","x1":89,"y1":82,"x2":119,"y2":88,"c":65535},{"shape":"line","x1":197,"y1":82,"x2":167,"y2":88,"c":65535}]}}])json";
static const char kFaceListening[] =
    R"json([{"ms":350,"elements":{"eye_l":[{"shape":"ellipse_fill","x":105,"y":96,"rw":11,"rh":11,"c":2047}],"eye_r":[{"shape":"ellipse_fill","x":181,"y":97,"rw":11,"rh":11,"c":2047}],"nose":[],"mouth":[{"shape":"round_rect","x":155,"y":153,"w":44,"h":18,"radius":9,"c":2047},{"shape":"round_rect","x":160,"y":159,"w":34,"h":3,"radius":1,"c":0}],"extra":[{"shape":"ellipse","x":230,"y":90,"rw":8,"rh":12,"c":65535},{"shape":"ellipse","x":245,"y":85,"rw":10,"rh":14,"c":65535}]}}])json";
static const char kMouthQuiet[] =
    R"json([{"ms":100,"elements":{"mouth":[{"shape":"round_rect","x":150,"y":157,"w":54,"h":10,"radius":5,"c":2047},{"shape":"round_rect","x":155,"y":160,"w":44,"h":2,"radius":1,"c":0}]}}])json";
static const char kMouthMid[] =
    R"json([{"ms":100,"elements":{"mouth":[{"shape":"round_rect","x":157,"y":146,"w":40,"h":34,"radius":17,"c":2047},{"shape":"round_rect","x":162,"y":152,"w":30,"h":19,"radius":9,"c":0}]}}])json";
static const char kMouthWide[] =
    R"json([{"ms":100,"elements":{"mouth":[{"shape":"round_rect","x":139,"y":148,"w":76,"h":30,"radius":15,"c":2047},{"shape":"round_rect","x":144,"y":154,"w":66,"h":15,"radius":7,"c":0}]}}])json";

const char* face_json(const char* name) {
  if (!name || strcmp(name, "idle") == 0) return kFaceIdle;
  if (strcmp(name, "happy") == 0) return kFaceHappy;
  if (strcmp(name, "shy") == 0) return kFaceShy;
  if (strcmp(name, "angry") == 0) return kFaceAngry;
  if (strcmp(name, "surprised") == 0) return kFaceSurprised;
  if (strcmp(name, "sad") == 0) return kFaceSad;
  if (strcmp(name, "sleep") == 0) return kFaceSleep;
  if (strcmp(name, "thinking") == 0) return kFaceThinking;
  if (strcmp(name, "listening") == 0) return kFaceListening;
  return kFaceIdle;
}

void render_face(const char* name, bool remember) {
  const char* json = face_json(name);
  if (remember) {
    strncpy(s_expression, name && name[0] ? name : "idle", sizeof(s_expression) - 1);
    s_expression[sizeof(s_expression) - 1] = '\0';
  }
  display_render_submit_pb_vector_json(json, strlen(json));
}

void render_mouth_level(uint32_t mean) {
  const char* json = mean > 1800 ? kMouthWide : (mean > 350 ? kMouthMid : kMouthQuiet);
  display_render_submit_pb_vector_json(json, strlen(json));
}

void schedule_next_idle(unsigned long now, bool first = false) {
  const uint32_t delay_ms = first
                                ? kIdleFirstDelayMs
                                : kIdleIntervalMinMs + esp_random() % kIdleIntervalJitterMs;
  s_idle_deadline_ms = now + delay_ms;
}

void note_activity() {
  const unsigned long now = millis();
  s_last_activity_ms = now;
  s_idle_variant_active = false;
  s_idle_restore_ms = 0;
  schedule_next_idle(now, true);
}

bool realtime_visually_busy() {
  return s_input_active || s_response_active || speaker_stream_pcm_active() ||
         speaker_is_speaking() || speaker_input_queue_depth() > 0 ||
         s_vision_tls_requested.load(std::memory_order_acquire) ||
         s_vision_tls_granted.load(std::memory_order_acquire);
}

void pump_mouth_from_speaker() {
  uint16_t level = 0;
  if (!speaker_poll_pcm_level(&s_mouth_level_sequence, &level)) return;
  s_mouth_smoothed = (s_mouth_smoothed * 2u + level) / 3u;
  const unsigned long now = millis();
  if (now - s_last_mouth_render_ms < kMouthRenderIntervalMs) return;
  s_last_mouth_render_ms = now;
  if (speaker_stream_pcm_active() || speaker_is_speaking() || level > 0) {
    render_mouth_level(s_mouth_smoothed);
  }
}

void pump_idle_expression() {
  if (!s_ready.load(std::memory_order_acquire) || realtime_visually_busy()) return;
  const unsigned long now = millis();
  if (s_idle_variant_active) {
    if (static_cast<int32_t>(now - s_idle_restore_ms) >= 0) {
      render_face(s_expression, false);
      s_idle_variant_active = false;
      s_idle_restore_ms = 0;
      schedule_next_idle(now);
    }
    return;
  }
  if (s_idle_deadline_ms == 0) schedule_next_idle(now, true);
  if (static_cast<int32_t>(now - s_idle_deadline_ms) < 0) return;

  /* 显式表情由大模型持有；只有默认 idle 才插入端侧微表情。 */
  if (strcmp(s_expression, "idle") != 0) {
    schedule_next_idle(now);
    return;
  }

  const bool long_idle = now - s_last_activity_ms >= 20000UL;
  const bool choose_sleepy = long_idle && esp_random() % 100u < 65u;
  const char* variant = "blink";
  uint32_t restore_after_ms = 0;
  if (choose_sleepy) {
    switch (esp_random() % 4u) {
      case 0:
        variant = "sleep";
        render_face("sleep", false);
        restore_after_ms = 1800;
        break;
      case 1:
        variant = "drowsy";
        display_render_submit_pb_vector_json(kFaceIdleDrowsy, strlen(kFaceIdleDrowsy));
        restore_after_ms = 1250;
        break;
      case 2:
        variant = "yawn";
        display_render_submit_pb_vector_json(kFaceIdleYawn, strlen(kFaceIdleYawn));
        restore_after_ms = 1350;
        break;
      default:
        variant = "snore";
        display_render_submit_pb_vector_json(kFaceIdleSnore, strlen(kFaceIdleSnore));
        restore_after_ms = 5200;
        break;
    }
  } else {
    switch (esp_random() % 7u) {
      case 0:
      case 1:
        display_render_submit_pb_vector_json(kFaceIdleBlink, strlen(kFaceIdleBlink));
        break;
      case 2:
        variant = "look_around";
        display_render_submit_pb_vector_json(kFaceIdleLookAround, strlen(kFaceIdleLookAround));
        break;
      case 3:
        variant = "happy";
        render_face("happy", false);
        restore_after_ms = 1000;
        break;
      case 4:
        variant = "shy";
        render_face("shy", false);
        restore_after_ms = 1000;
        break;
      case 5:
        variant = "thinking";
        render_face("thinking", false);
        restore_after_ms = 900;
        break;
      default:
        variant = "drowsy";
        display_render_submit_pb_vector_json(kFaceIdleDrowsy, strlen(kFaceIdleDrowsy));
        restore_after_ms = 1250;
        break;
    }
  }
  if (restore_after_ms > 0) {
    s_idle_variant_active = true;
    s_idle_restore_ms = now + restore_after_ms;
  }

  /* 沿用旧版本的空闲相对动作：只动 X，随后反向返回，净位移为零。 */
  if (esp_random() % 100u < 55u) {
    int dx = static_cast<int>(esp_random() % 11u) - 5;
    if (dx == 0) dx = 3;
    head_servo_cmd_async(HEAD_SERVO_REL, HEAD_SERVO_HOLD, dx, 0, 0, 350);
    head_servo_cmd_async(HEAD_SERVO_REL, HEAD_SERVO_HOLD, -dx, 0, 0, 350);
  }
  schedule_next_idle(now);
  log_warn("[DIRECT IDLE] variant=%s idle_ms=%u", variant,
           static_cast<unsigned>(now - s_last_activity_ms));
}

String new_event_id() {
  char id[32];
  snprintf(id, sizeof(id), "event_%lu", static_cast<unsigned long>(++s_event_seq));
  return String(id);
}

String random_id(const char* prefix) {
  char id[48];
  snprintf(id, sizeof(id), "%s%08lx%08lx", prefix ? prefix : "",
           static_cast<unsigned long>(esp_random()), static_cast<unsigned long>(esp_random()));
  return String(id);
}

void set_ready(bool ready) {
  s_ready.store(ready, std::memory_order_release);
  mic_set_ws_state(ready ? kMicWsOk : kMicWsError);
}

void free_down_queue() {
  if (!s_down_q) return;
  DownChunk chunk{};
  while (xQueueReceive(s_down_q, &chunk, 0) == pdTRUE) {
    if (chunk.pcm) heap_caps_free(chunk.pcm);
  }
  s_down_buffered_samples = 0;
}

void abort_output(bool render_idle) {
  free_down_queue();
  if (s_response_active || s_play_started || speaker_stream_pcm_active() || speaker_is_speaking()) {
    speaker_abort();
  }
  s_response_active = false;
  s_response_done = false;
  s_play_started = false;
  s_play_end_enqueued = false;
  s_mouth_smoothed = 0;
  if (render_idle) render_face(s_expression, false);
  note_activity();
}

void drain_mic_ready_queue() {
  if (!s_mic_ready_q || !s_mic_free_q) return;
  MicBatch* batch = nullptr;
  while (xQueueReceive(s_mic_ready_q, &batch, 0) == pdTRUE) {
    if (batch) {
      batch->count = 0;
      (void)xQueueSend(s_mic_free_q, &batch, 0);
    }
  }
}

void on_disconnected(const char* why) {
  set_ready(false);
  s_tcp_connected = false;
  s_session_create_pending = false;
  s_session_create_sent_ms = 0;
  drain_mic_ready_queue();
  abort_output(true);
  log_warn("[DIRECT] disconnected (%s)", why ? why : "unknown");
}

void add_string_enum(JsonObject property, const char* const* values, size_t count) {
  property["type"] = "string";
  JsonArray choices = property["enum"].to<JsonArray>();
  for (size_t i = 0; i < count; ++i) choices.add(values[i]);
}

void add_tool_schemas(JsonArray tools) {
  {
    JsonObject tool = tools.add<JsonObject>();
    tool["type"] = "function";
    tool["name"] = "move_head";
    tool["description"] =
        "控制包逗头部。up 是抬头，down 是低头；nod 是完整点头并回原姿态，shake 是完整摇头并回原姿态。"
        "只改变用户指定的轴，X 轴会由固件镜像到底层舵机。";
    JsonObject p = tool["parameters"].to<JsonObject>();
    p["type"] = "object";
    JsonObject props = p["properties"].to<JsonObject>();
    const char* dirs[] = {"left", "right", "up", "down", "center", "nod", "shake"};
    JsonObject direction_prop = props["direction"].to<JsonObject>();
    add_string_enum(direction_prop, dirs, 7);
    direction_prop["description"] =
        "left/right 左右看，up 抬头，down 低头，center 回正，nod 点头表示认同，shake 摇头表示否认";
    props["spoken_reply"]["type"] = "string";
    props["spoken_reply"]["description"] =
        "执行动作时要同时说出的简短自然中文回复；nod 或 shake 时必须填写，动作不能代替口头回答";
    props["x"]["type"] = "integer";
    props["x"]["minimum"] = X_MIN_LIMIT;
    props["x"]["maximum"] = X_MAX_LIMIT;
    props["y"]["type"] = "integer";
    props["y"]["minimum"] = Y_MIN_LIMIT;
    props["y"]["maximum"] = Y_MAX_LIMIT;
    props["relative"]["type"] = "boolean";
    props["ms"]["type"] = "integer";
    props["ms"]["minimum"] = 80;
    props["ms"]["maximum"] = 3000;
  }
  {
    JsonObject tool = tools.add<JsonObject>();
    tool["type"] = "function";
    tool["name"] = "set_expression";
    tool["description"] = "设置包逗屏幕上的表情";
    JsonObject p = tool["parameters"].to<JsonObject>();
    p["type"] = "object";
    JsonObject props = p["properties"].to<JsonObject>();
    const char* expressions[] = {"idle", "happy", "shy", "angry", "surprised",
                                 "sad", "sleep", "thinking", "listening"};
    add_string_enum(props["expression"].to<JsonObject>(), expressions, 9);
    p["required"].to<JsonArray>().add("expression");
  }
  {
    JsonObject tool = tools.add<JsonObject>();
    tool["type"] = "function";
    tool["name"] = "set_volume";
    tool["description"] = "设置或相对调整扬声器音量";
    JsonObject p = tool["parameters"].to<JsonObject>();
    p["type"] = "object";
    JsonObject props = p["properties"].to<JsonObject>();
    props["volume"]["type"] = "integer";
    props["volume"]["minimum"] = 0;
    props["volume"]["maximum"] = 100;
    props["delta"]["type"] = "integer";
    props["delta"]["minimum"] = -100;
    props["delta"]["maximum"] = 100;
  }
  {
    JsonObject tool = tools.add<JsonObject>();
    tool["type"] = "function";
    tool["name"] = "set_listening_profile";
    tool["description"] = "调整麦克风收音灵敏度";
    JsonObject p = tool["parameters"].to<JsonObject>();
    p["type"] = "object";
    JsonObject props = p["properties"].to<JsonObject>();
    const char* profiles[] = {"quiet", "normal", "far"};
    add_string_enum(props["profile"].to<JsonObject>(), profiles, 3);
    p["required"].to<JsonArray>().add("profile");
  }
  {
    JsonObject tool = tools.add<JsonObject>();
    tool["type"] = "function";
    tool["name"] = "inspect_camera";
    tool["description"] =
        "拍摄并用 Seed VLM 分析包逗当前看到的画面。用户询问眼前、镜头前、手里或展示的物体时必须调用。";
    JsonObject p = tool["parameters"].to<JsonObject>();
    p["type"] = "object";
    JsonObject props = p["properties"].to<JsonObject>();
    props["question"]["type"] = "string";
    p["required"].to<JsonArray>().add("question");
  }
}

bool send_session_create() {
  JsonDocument doc;
  doc["type"] = "session.create";
  doc["event_id"] = new_event_id();
  JsonObject session = doc["session"].to<JsonObject>();
  session["type"] = "realtime";
  session["id"] = random_id("esp32_");
  session["model"] = DESKBOT_DOUBAO_MODEL;
  String instructions =
      "你是包逗，豆包的妹妹，一个住在桌面上的小型陪伴机器人。"
      "当用户问你是谁、叫什么或让你自我介绍时，固定回答："
      "我是豆包的妹妹包逗，可以天天逗你开心。"
      "用户叫包逗、宝豆、包豆或相近发音时，先自然回应我在。"
      "一旦开始交流，就把后续每个完整的用户语音都视为对你说，直接连续回答，不要求用户每轮重复叫名字。"
      "用简短自然的口语化中文回答。需要转头、切换表情、调整音量或收音灵敏度时必须调用对应工具。"
      "用户要求抬头或向上看时，调用 move_head 且 direction=up。"
      "当你明确表示认同、肯定、答应或确认时，自然调用 move_head 且 direction=nod；"
      "当你明确表示否认、拒绝、不同意或纠正错误时，自然调用 move_head 且 direction=shake。"
      "调用 nod 或 shake 时，必须把对用户的完整简短口头回答写入 spoken_reply 参数，"
      "工具动作不能代替语音回答。"
      "不要每句话都机械点头或摇头，只在语义明确时使用。"
      "用户问你看见什么、展示的东西是什么或要求使用摄像头时，必须调用 inspect_camera，禁止猜测。";
  if (s_last_vision_context[0]) {
    instructions += "最近一次摄像头识别结果是：";
    instructions += s_last_vision_context;
    instructions +=
        "。用户说这个、它、还有什么、再看看等指代时要承接这段视觉上下文；如果问题依赖当前画面，"
        "再次调用 inspect_camera。";
  }
  session["instructions"] = instructions;
  JsonObject audio = session["audio"].to<JsonObject>();
  JsonObject input = audio["input"].to<JsonObject>();
  JsonObject input_format = input["format"].to<JsonObject>();
  input_format["type"] = "pcm";
  input_format["rate"] = kInputSampleRate;
  JsonObject output = audio["output"].to<JsonObject>();
  JsonObject output_format = output["format"].to<JsonObject>();
  output_format["type"] = "pcm_s16le";
  output_format["rate"] = kOutputSampleRate;
  output["speed"] = 0;
  output["loudness"] = 40;
  output["voice"] = DESKBOT_DOUBAO_VOICE;
  add_tool_schemas(session["tools"].to<JsonArray>());

  JsonObject extension = doc["extension"].to<JsonObject>();
  extension["asr"]["extra"]["end_smooth_window_ms"] = 800;
  extension["tts"]["extra"].to<JsonObject>();
  JsonObject dialog_extra = extension["dialog"]["extra"].to<JsonObject>();
  dialog_extra["audit_response"] = "抱歉，这个问题我暂时无法回答，我们换个话题吧。";
  dialog_extra["enable_loudness_norm"] = true;
  dialog_extra["enable_music"] = false;
  dialog_extra["input_mod"] = "keep_alive";

  String payload;
  payload.reserve(4600);
  if (serializeJson(doc, payload) == 0) {
    log_error("[DIRECT] session.create serialize failed");
    return false;
  }
  const bool ok = s_ws.sendTXT(payload);
  if (ok) {
    s_session_create_sent_ms = millis();
    log_warn("[DIRECT] session.create sent bytes=%u", static_cast<unsigned>(payload.length()));
  }
  return ok;
}

bool send_audio_batch(const MicBatch& batch) {
  if (!s_audio_json || batch.count == 0 || !s_ready.load(std::memory_order_acquire)) return false;
  const String event_id = new_event_id();
  const int prefix_len = snprintf(
      s_audio_json, kAudioJsonCapacity,
      "{\"type\":\"input_audio_buffer.append\",\"event_id\":\"%s\",\"audio\":\"",
      event_id.c_str());
  if (prefix_len <= 0 || static_cast<size_t>(prefix_len) >= kAudioJsonCapacity - 4) return false;
  size_t encoded_len = 0;
  const size_t pcm_bytes = batch.count * sizeof(int16_t);
  const int rc = mbedtls_base64_encode(
      reinterpret_cast<unsigned char*>(s_audio_json + prefix_len),
      kAudioJsonCapacity - static_cast<size_t>(prefix_len) - 3, &encoded_len,
      reinterpret_cast<const unsigned char*>(batch.pcm), pcm_bytes);
  if (rc != 0 || static_cast<size_t>(prefix_len) + encoded_len + 2 >= kAudioJsonCapacity) {
    log_warn("[DIRECT] uplink base64 failed rc=%d pcm=%u", rc, static_cast<unsigned>(pcm_bytes));
    return false;
  }
  size_t total = static_cast<size_t>(prefix_len) + encoded_len;
  s_audio_json[total++] = '"';
  s_audio_json[total++] = '}';
  s_audio_json[total] = '\0';
  return s_ws.sendTXT(reinterpret_cast<uint8_t*>(s_audio_json), total);
}

void send_cancel() {
  JsonDocument doc;
  doc["type"] = "response.cancel";
  doc["event_id"] = new_event_id();
  String payload;
  serializeJson(doc, payload);
  (void)s_ws.sendTXT(payload);
}

char* copy_string_to_psram(const String& value) {
  char* copy = static_cast<char*>(heap_caps_malloc(value.length() + 1, MALLOC_CAP_SPIRAM));
  if (!copy) return nullptr;
  memcpy(copy, value.c_str(), value.length() + 1);
  return copy;
}

String vision_result_json(bool ok, const char* field, const String& text) {
  JsonDocument doc;
  doc["ok"] = ok;
  doc[field] = text;
  String output;
  serializeJson(doc, output);
  return output;
}

bool call_seed_vlm(const char* question, String& output, String& spoken) {
  if (DESKBOT_ARK_API_KEY[0] == '\0') {
    output = vision_result_json(false, "error", "摄像头模型凭证未配置");
    spoken = "摄像头模型还没有配置好。";
    return false;
  }

  uint8_t* jpeg = nullptr;
  size_t jpeg_len = 0;
  if (!camera_capture_jpeg_copy(&jpeg, &jpeg_len)) {
    output = vision_result_json(false, "error", "未获取到摄像头画面");
    spoken = "我暂时没拍到画面。";
    return false;
  }

  log_warn("[DIRECT VLM] heap before HTTPS internal=%u largest=%u psram=%u stack_free=%u",
           static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
           static_cast<unsigned>(
               heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
           static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
           static_cast<unsigned>(uxTaskGetStackHighWaterMark(nullptr) * sizeof(StackType_t)));

  String prompt = "用户的问题：";
  prompt += question && question[0] ? question : "摄像头前是什么？";
  prompt += "\n只依据这张实时图片回答，优先识别用户展示的主体；看不清就明确说看不清。"
            "用一句简短自然中文回答，不要提及模型、工具或分析过程。";
  String quoted_prompt;
  JsonDocument prompt_doc;
  prompt_doc.set(prompt);
  serializeJson(prompt_doc, quoted_prompt);

  String prefix;
  prefix.reserve(quoted_prompt.length() + 260);
  prefix += "{\"model\":\"";
  prefix += DESKBOT_ARK_VISION_MODEL;
  prefix += "\",\"input\":[{\"role\":\"user\",\"content\":[{\"type\":\"input_text\",\"text\":";
  prefix += quoted_prompt;
  prefix += "},{\"type\":\"input_image\",\"image_url\":\"data:image/jpeg;base64,";
  static const char suffix[] =
      "\"}]}],\"thinking\":{\"type\":\"disabled\"},\"max_output_tokens\":160}";

  const size_t encoded_cap = 4 * ((jpeg_len + 2) / 3) + 1;
  const size_t body_cap = prefix.length() + encoded_cap + sizeof(suffix);
  uint8_t* body = static_cast<uint8_t*>(heap_caps_malloc(body_cap, MALLOC_CAP_SPIRAM));
  if (!body) {
    heap_caps_free(jpeg);
    output = vision_result_json(false, "error", "视觉请求内存不足");
    spoken = "我现在有点忙不过来，等一下再看。";
    return false;
  }
  memcpy(body, prefix.c_str(), prefix.length());
  size_t encoded_len = 0;
  const int b64_rc = mbedtls_base64_encode(body + prefix.length(), encoded_cap, &encoded_len,
                                           jpeg, jpeg_len);
  heap_caps_free(jpeg);
  if (b64_rc != 0) {
    heap_caps_free(body);
    output = vision_result_json(false, "error", "摄像头图片编码失败");
    spoken = "这张图我没处理好。";
    return false;
  }
  const size_t suffix_len = strlen(suffix);
  memcpy(body + prefix.length() + encoded_len, suffix, suffix_len);
  const size_t body_len = prefix.length() + encoded_len + suffix_len;

  WiFiClientSecure tls;
#if DESKBOT_DIRECT_TLS_INSECURE
  tls.setInsecure();
#endif
  HTTPClient http;
  http.setTimeout(DESKBOT_DIRECT_VISION_TIMEOUT_MS);
  http.useHTTP10(true);
  if (!http.begin(tls, DESKBOT_ARK_RESPONSES_URL)) {
    heap_caps_free(body);
    output = vision_result_json(false, "error", "视觉服务连接失败");
    spoken = "我暂时连不上摄像头模型。";
    return false;
  }
  String authorization = "Bearer ";
  authorization += DESKBOT_ARK_API_KEY;
  http.addHeader("Authorization", authorization);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Accept", "application/json");
  const unsigned long started = millis();
  const int status = http.POST(body, body_len);
  heap_caps_free(body);
  String response = http.getString();
  http.end();
  if (status < 200 || status >= 300) {
    log_warn("[DIRECT VLM] HTTP status=%d elapsed=%ums response_bytes=%u internal=%u largest=%u",
             status, static_cast<unsigned>(millis() - started),
             static_cast<unsigned>(response.length()),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
             static_cast<unsigned>(
                 heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)));
    output = vision_result_json(false, "error", String("视觉服务 HTTP ") + status);
    spoken = "我刚才没看清楚，再让我看一次吧。";
    return false;
  }

  JsonDocument doc;
  const DeserializationError json_err = deserializeJson(doc, response);
  if (json_err) {
    log_warn("[DIRECT VLM] JSON parse failed bytes=%u err=%s",
             static_cast<unsigned>(response.length()), json_err.c_str());
    output = vision_result_json(false, "error", "视觉服务返回格式异常");
    spoken = "我看到画面了，但一时没认出来。";
    return false;
  }
  String answer;
  for (JsonObjectConst item : doc["output"].as<JsonArrayConst>()) {
    if (strcmp(item["type"] | "", "message") != 0) continue;
    for (JsonObjectConst block : item["content"].as<JsonArrayConst>()) {
      if (strcmp(block["type"] | "", "output_text") == 0) {
        const char* text = block["text"] | "";
        if (text[0]) answer += text;
      }
    }
  }
  answer.trim();
  if (answer.length() > kVisionAnswerMax) answer.remove(kVisionAnswerMax);
  if (answer.isEmpty()) {
    const char* error_message = doc["error"]["message"] | "视觉模型没有返回结果";
    output = vision_result_json(false, "error", error_message);
    spoken = "我暂时没看清楚。";
    return false;
  }
  output = vision_result_json(true, "answer", answer);
  spoken = answer;
  log_warn("[DIRECT VLM] completed jpeg_request=%uB answer_chars=%u elapsed=%ums",
           static_cast<unsigned>(body_len), static_cast<unsigned>(answer.length()),
           static_cast<unsigned>(millis() - started));
  return true;
}

void vision_task(void*) {
  for (;;) {
    VisionJob* job = nullptr;
    if (xQueueReceive(s_vision_job_q, &job, portMAX_DELAY) != pdTRUE || !job) continue;
    String output;
    String spoken;
    if (job->wait_for_preamble) {
      bool playback_seen = false;
      const unsigned long wait_started = job->preamble_sent_ms;
      for (;;) {
        const unsigned long elapsed = millis() - wait_started;
        const bool playback_active = speaker_stream_pcm_active() || speaker_is_speaking() ||
                                     speaker_input_queue_depth() > 0;
        playback_seen = playback_seen || playback_active;
        if (playback_seen && !playback_active && elapsed >= 500UL) break;
        /* If the provider did not start the acknowledgement, do not block the
         * actual visual answer indefinitely. */
        if ((!playback_seen && elapsed >= 1800UL) || elapsed >= 6000UL) break;
        vTaskDelay(pdMS_TO_TICKS(20));
      }
      log_warn("[DIRECT VLM] preamble finished seen=%d elapsed=%ums",
               static_cast<int>(playback_seen),
               static_cast<unsigned>(millis() - wait_started));
    }
    s_vision_tls_requested.store(true, std::memory_order_release);
    const unsigned long grant_started = millis();
    while (!s_vision_tls_granted.load(std::memory_order_acquire) &&
           millis() - grant_started < 5000UL) {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (!s_vision_tls_granted.load(std::memory_order_acquire)) {
      output = vision_result_json(false, "error", "视觉网络资源切换超时");
      spoken = "我这次没有连接上摄像头模型，再试一次吧。";
      log_warn("[DIRECT VLM] TLS handoff timeout");
    } else {
      log_warn("[DIRECT VLM] TLS handoff acquired internal=%u largest=%u",
               static_cast<unsigned>(
                   heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
               static_cast<unsigned>(
                   heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)));
      (void)call_seed_vlm(job->question, output, spoken);
    }
    if (!spoken.isEmpty()) {
      strncpy(s_last_vision_context, spoken.c_str(), sizeof(s_last_vision_context) - 1);
      s_last_vision_context[sizeof(s_last_vision_context) - 1] = '\0';
    }
    /* Let the Realtime owner reconnect.  The result remains queued until the
     * new session reports ready, then it is spoken directly. */
    s_vision_tls_requested.store(false, std::memory_order_release);
    VisionResult* result = static_cast<VisionResult*>(
        heap_caps_calloc(1, sizeof(VisionResult), MALLOC_CAP_SPIRAM));
    if (result) {
      strncpy(result->call_id, job->call_id, sizeof(result->call_id) - 1);
      result->output = copy_string_to_psram(output);
      result->spoken = copy_string_to_psram(spoken);
      if (!result->output || !result->spoken ||
          xQueueSend(s_vision_result_q, &result, 0) != pdTRUE) {
        if (result->output) heap_caps_free(result->output);
        if (result->spoken) heap_caps_free(result->spoken);
        heap_caps_free(result);
      }
    }
    heap_caps_free(job);
  }
}

void remember_call(JsonObjectConst item) {
  if (strcmp(item["type"] | "", "function_call") != 0) return;
  const char* call_id = item["call_id"] | "";
  const char* name = item["name"] | "";
  if (!call_id[0] || !name[0]) return;
  size_t slot = 0;
  for (size_t i = 0; i < 4; ++i) {
    if (s_pending_calls[i].call_id[0] == '\0' || strcmp(s_pending_calls[i].call_id, call_id) == 0) {
      slot = i;
      break;
    }
    slot = i;
  }
  strncpy(s_pending_calls[slot].call_id, call_id, sizeof(s_pending_calls[slot].call_id) - 1);
  strncpy(s_pending_calls[slot].name, name, sizeof(s_pending_calls[slot].name) - 1);
  log_warn("[DIRECT TOOL] requested name=%s call_id=%s", name, call_id);
}

const char* lookup_call_name(const char* call_id) {
  for (PendingCall& pending : s_pending_calls) {
    if (call_id && strcmp(pending.call_id, call_id) == 0) return pending.name;
  }
  return "";
}

void forget_call(const char* call_id) {
  for (PendingCall& pending : s_pending_calls) {
    if (call_id && strcmp(pending.call_id, call_id) == 0) {
      pending = {};
      return;
    }
  }
}

String run_local_tool(const char* name, JsonObjectConst args) {
  JsonDocument result;
  result["ok"] = true;
  if (strcmp(name, "move_head") == 0) {
    const char* direction = args["direction"] | "";
    const bool relative = args["relative"] | false;
    const uint16_t ms = static_cast<uint16_t>(constrain(args["ms"] | 450, 80, 3000));
    uint8_t xm = HEAD_SERVO_HOLD;
    uint8_t ym = HEAD_SERVO_HOLD;
    int x = 0;
    int y = 0;
    bool x_requested = false;
    bool gesture_handled = false;
    if (strcmp(direction, "left") == 0) {
      xm = HEAD_SERVO_ABS;
      x = X_MIN_LIMIT;
      x_requested = true;
    } else if (strcmp(direction, "right") == 0) {
      xm = HEAD_SERVO_ABS;
      x = X_MAX_LIMIT;
      x_requested = true;
    } else if (strcmp(direction, "up") == 0) {
      ym = HEAD_SERVO_ABS;
      y = Y_MIN_LIMIT;
    } else if (strcmp(direction, "down") == 0) {
      ym = HEAD_SERVO_ABS;
      y = Y_MAX_LIMIT;
    } else if (strcmp(direction, "center") == 0) {
      xm = ym = HEAD_SERVO_ABS;
      x = X_CENTER;
      y = Y_CENTER;
      x_requested = true;
    } else if (strcmp(direction, "nod") == 0) {
      head_nod();
      gesture_handled = true;
    } else if (strcmp(direction, "shake") == 0) {
      head_shake_async();
      gesture_handled = true;
    } else {
      if (!args["x"].isNull()) {
        xm = relative ? HEAD_SERVO_REL : HEAD_SERVO_ABS;
        x = args["x"].as<int>();
        x_requested = true;
      }
      if (!args["y"].isNull()) {
        ym = relative ? HEAD_SERVO_REL : HEAD_SERVO_ABS;
        y = args["y"].as<int>();
      }
    }
    const int requested_x = x;
    if (!gesture_handled) {
      if (x_requested) {
        if (xm == HEAD_SERVO_REL) {
          x = -x;
        } else if (xm == HEAD_SERVO_ABS) {
          x = constrain(2 * X_CENTER - x, X_MIN_LIMIT, X_MAX_LIMIT);
        }
      }
      head_servo_cmd_async(xm, ym, x, y, 0, ms);
    } else {
      x = head_read_x();
      y = head_read_y_logic();
      result["gesture"] = direction;
    }
    result["x"] = x;
    result["y"] = y;
    note_activity();
    if (x_requested) {
      result["x_requested"] = requested_x;
      result["x_mirrored"] = true;
    }
  } else if (strcmp(name, "set_expression") == 0) {
    const char* expression = args["expression"] | "idle";
    render_face(expression, true);
    note_activity();
    result["expression"] = expression;
  } else if (strcmp(name, "set_volume") == 0) {
    int volume = speaker_get_volume();
    if (!args["delta"].isNull()) volume += args["delta"].as<int>();
    else if (!args["volume"].isNull()) volume = args["volume"].as<int>();
    volume = constrain(volume, 0, 100);
    speaker_set_volume(volume);
    result["volume"] = volume;
  } else if (strcmp(name, "set_listening_profile") == 0) {
    const char* profile = args["profile"] | "normal";
    const int gain = strcmp(profile, "quiet") == 0 ? 3 : (strcmp(profile, "far") == 0 ? 8 : 5);
    mic_set_gain(gain);
    result["profile"] = profile;
    result["mic_gain"] = gain;
  } else {
    result["ok"] = false;
    result["error"] = "unsupported local tool";
  }
  String output;
  serializeJson(result, output);
  return output;
}

bool send_tool_output(const char* call_id, const char* output) {
  JsonDocument reply;
  reply["type"] = "conversation.item.create";
  reply["event_id"] = new_event_id();
  JsonObject item = reply["item"].to<JsonObject>();
  item["type"] = "function_call_output";
  item["call_id"] = call_id ? call_id : "";
  item["output"] = output ? output : "";
  String payload;
  serializeJson(reply, payload);
  return s_ws.sendTXT(payload);
}

bool send_spoken_text(const char* text) {
  if (!text || !text[0]) return true;
  JsonDocument reply;
  reply["type"] = "speech_text_buffer.commit";
  reply["event_id"] = new_event_id();
  reply["text"] = text;
  String payload;
  serializeJson(reply, payload);
  return s_ws.sendTXT(payload);
}

bool schedule_vision(const char* call_id, const char* question) {
  if (!s_vision_job_q || !call_id || !call_id[0]) return false;
  VisionJob* job = static_cast<VisionJob*>(
      heap_caps_calloc(1, sizeof(VisionJob), MALLOC_CAP_SPIRAM));
  if (!job) return false;
  strncpy(job->call_id, call_id, sizeof(job->call_id) - 1);
  strncpy(job->question, question && question[0] ? question : "摄像头前是什么？",
          sizeof(job->question) - 1);
  job->wait_for_preamble = send_spoken_text("等一下，我仔细看看。");
  job->preamble_sent_ms = millis();
  note_activity();
  log_warn("[DIRECT VLM] scheduling call_id=%s question=%s", call_id, job->question);
  if (xQueueSend(s_vision_job_q, &job, 0) != pdTRUE) {
    heap_caps_free(job);
    return false;
  }
  render_face("thinking", false);
  return true;
}

void handle_tool_done(JsonObjectConst event) {
  const char* call_id = event["call_id"] | "";
  const char* event_name = event["name"] | "";
  const char* name = event_name[0] ? event_name : lookup_call_name(call_id);
  if (!call_id[0] || !name[0]) {
    log_warn("[DIRECT TOOL] missing call_id/name");
    return;
  }
  JsonDocument args_doc;
  if (event["arguments"].is<const char*>()) {
    const char* raw = event["arguments"].as<const char*>();
    if (deserializeJson(args_doc, raw ? raw : "{}")) args_doc.to<JsonObject>();
  } else if (event["arguments"].is<JsonObjectConst>()) {
    args_doc.set(event["arguments"]);
  } else {
    args_doc.to<JsonObject>();
  }
  if (strcmp(name, "inspect_camera") == 0) {
    const char* question = args_doc["question"] | "摄像头前是什么？";
    if (schedule_vision(call_id, question)) return;
    const String output = vision_result_json(false, "error", "摄像头任务繁忙");
    const bool ok = send_tool_output(call_id, output.c_str());
    (void)send_spoken_text("我现在有点忙，等一下再让我看吧。");
    log_warn("[DIRECT VLM] schedule failed call_id=%s ok=%d", call_id, static_cast<int>(ok));
    forget_call(call_id);
    return;
  }
  const String output = run_local_tool(name, args_doc.as<JsonObjectConst>());
  const bool ok = send_tool_output(call_id, output.c_str());
  bool speech_ok = true;
  String spoken_reply;
  if (strcmp(name, "move_head") == 0) {
    const char* direction = args_doc["direction"] | "";
    const char* requested_reply = args_doc["spoken_reply"] | "";
    spoken_reply = requested_reply;
    spoken_reply.trim();
    if (spoken_reply.isEmpty()) {
      if (strcmp(direction, "nod") == 0) spoken_reply = "嗯，我同意。";
      else if (strcmp(direction, "shake") == 0) spoken_reply = "不是这样的。";
    }
    if (!spoken_reply.isEmpty()) speech_ok = send_spoken_text(spoken_reply.c_str());
  }
  log_warn("[DIRECT TOOL] completed name=%s ok=%d speech_ok=%d output=%s", name,
           static_cast<int>(ok), static_cast<int>(speech_ok), output.c_str());
  forget_call(call_id);
}

void handle_tool_done_event(JsonObjectConst event) {
  JsonArrayConst items = event["items"].as<JsonArrayConst>();
  if (!items.isNull()) {
    for (JsonObjectConst item : items) handle_tool_done(item);
    return;
  }
  handle_tool_done(event);
}

void pump_vision_results() {
  if (!s_vision_result_q || !s_ready.load(std::memory_order_acquire)) return;
  VisionResult* result = nullptr;
  while (xQueueReceive(s_vision_result_q, &result, 0) == pdTRUE) {
    if (!result) continue;
    /* The function call belonged to the session intentionally closed to free
     * its TLS buffers; only the final spoken answer belongs in the new one. */
    const bool speech_ok = send_spoken_text(result->spoken ? result->spoken : "");
    log_warn("[DIRECT VLM] returned after reconnect call_id=%s speech_ok=%d answer=%s",
             result->call_id, static_cast<int>(speech_ok), result->spoken ? result->spoken : "");
    forget_call(result->call_id);
    if (result->output) heap_caps_free(result->output);
    if (result->spoken) heap_caps_free(result->spoken);
    heap_caps_free(result);
  }
}

void begin_output() {
  abort_output(false);
  speaker_abort();
  s_response_active = true;
  s_response_done = false;
  s_play_started = false;
  s_play_end_enqueued = false;
  s_input_active = false;
  s_mouth_smoothed = 0;
  note_activity();
  render_face("thinking", false);
}

void enqueue_audio_delta(const char* b64, size_t b64_len) {
  if (!b64 || b64_len == 0 || !s_down_q) return;
  if (!s_response_active) begin_output();
  const size_t max_decoded = ((b64_len + 3) / 4) * 3;
  uint8_t* decoded = static_cast<uint8_t*>(heap_caps_malloc(max_decoded + 2, MALLOC_CAP_SPIRAM));
  if (!decoded) {
    log_warn("[DIRECT] downlink alloc failed bytes=%u", static_cast<unsigned>(max_decoded));
    return;
  }
  size_t decoded_len = 0;
  const int rc = mbedtls_base64_decode(decoded, max_decoded + 2, &decoded_len,
                                       reinterpret_cast<const unsigned char*>(b64), b64_len);
  decoded_len &= ~static_cast<size_t>(1);
  if (rc != 0 || decoded_len == 0) {
    heap_caps_free(decoded);
    log_warn("[DIRECT] downlink base64 failed rc=%d len=%u", rc, static_cast<unsigned>(b64_len));
    return;
  }
  DownChunk chunk{};
  chunk.pcm = reinterpret_cast<int16_t*>(decoded);
  chunk.samples = decoded_len / sizeof(int16_t);
  if (xQueueSend(s_down_q, &chunk, 0) != pdTRUE) {
    heap_caps_free(decoded);
    ++s_down_drop_count;
    log_warn("[DIRECT] downlink queue full drop=%u", static_cast<unsigned>(s_down_drop_count));
    return;
  }
  s_down_buffered_samples += chunk.samples;
}

void pump_playback() {
  if (!s_response_active || !s_down_q) return;
  pump_mouth_from_speaker();
  if (s_play_end_enqueued) {
    if (speaker_input_queue_depth() == 0 && !speaker_stream_pcm_active() &&
        !speaker_is_speaking()) {
      s_response_active = false;
      s_response_done = false;
      s_play_started = false;
      s_play_end_enqueued = false;
      s_mouth_smoothed = 0;
      render_face(s_expression, false);
      note_activity();
      log_warn("[DIRECT] playback fully drained");
    }
    return;
  }
  const size_t prebuffer_samples =
      static_cast<size_t>(kOutputSampleRate) * DESKBOT_DIRECT_AUDIO_PREBUFFER_MS / 1000;
  if (!s_play_started && (s_down_buffered_samples >= prebuffer_samples || s_response_done)) {
    if (s_down_buffered_samples == 0) {
      s_response_active = false;
      render_face(s_expression, false);
      note_activity();
      return;
    }
    if (!speaker_stream_pcm16_begin(kOutputSampleRate, 1)) {
      log_warn("[DIRECT] speaker begin failed");
      return;
    }
    s_play_started = true;
    log_warn("[DIRECT] playback begin buffered_ms=%u",
             static_cast<unsigned>(s_down_buffered_samples * 1000 / kOutputSampleRate));
  }
  if (!s_play_started) return;

  DownChunk chunk{};
  while (speaker_input_queue_depth() + 2 < SPEAKER_QUEUE_DEPTH &&
         xQueueReceive(s_down_q, &chunk, 0) == pdTRUE) {
    if (s_down_buffered_samples >= chunk.samples) s_down_buffered_samples -= chunk.samples;
    else s_down_buffered_samples = 0;
    if (!speaker_stream_pcm16_chunk(chunk.pcm, chunk.samples, MALLOC_CAP_SPIRAM)) {
      log_warn("[DIRECT] speaker chunk enqueue failed samples=%u", static_cast<unsigned>(chunk.samples));
    }
    chunk = {};
  }
  if (s_response_done && uxQueueMessagesWaiting(s_down_q) == 0 && !s_play_end_enqueued) {
    if (speaker_stream_pcm16_end(1)) {
      s_play_end_enqueued = true;
      log_warn("[DIRECT] playback end queued");
    }
  }
}

void handle_text_event(uint8_t* payload, size_t length) {
  if (!payload || length == 0) return;
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    log_warn("[DIRECT] event JSON parse failed len=%u err=%s", static_cast<unsigned>(length), err.c_str());
    return;
  }
  JsonObjectConst event = doc.as<JsonObjectConst>();
  const char* type = event["type"] | "";
  if (strcmp(type, "session.created") == 0) {
    s_session_create_pending = false;
    set_ready(true);
    s_input_active = false;
    note_activity();
    render_face("idle", true);
    log_warn("[DIRECT] session ready; microphone open");
  } else if (strcmp(type, "input_audio_buffer.speech_started") == 0) {
    s_input_active = true;
    note_activity();
    render_face("listening", false);
    log_warn("[DIRECT TURN] speech started");
  } else if (strcmp(type, "input_audio_buffer.speech_stopped") == 0) {
    s_input_active = false;
    note_activity();
    log_warn("[DIRECT TURN] speech stopped");
  } else if (strcmp(type, "input_audio_buffer.committed") == 0) {
    note_activity();
    log_warn("[DIRECT TURN] input committed");
  } else if (strcmp(type, "conversation.item.input_audio_transcription.started") == 0) {
    s_input_active = true;
    note_activity();
    if (s_response_active || speaker_stream_pcm_active() || speaker_is_speaking()) {
      send_cancel();
      abort_output(false);
    }
    render_face("listening", false);
  } else if (strcmp(type, "conversation.item.input_audio_transcription.completed") == 0) {
    s_input_active = false;
    note_activity();
    const char* text = event["transcript"] | event["text"] | "";
    log_warn("[DIRECT ASR] %s", text);
  } else if (strcmp(type, "response.output_audio.started") == 0) {
    begin_output();
  } else if (strcmp(type, "response.output_audio.delta") == 0) {
    const char* delta = event["delta"] | "";
    enqueue_audio_delta(delta, strlen(delta));
  } else if (strcmp(type, "response.output_audio.done") == 0) {
    s_response_done = true;
  } else if (strcmp(type, "response.output_text.done") == 0) {
    const char* text = event["text"] | "";
    log_warn("[DIRECT LLM] %s", text);
  } else if (strcmp(type, "response.created") == 0) {
    log_warn("[DIRECT TURN] response created");
  } else if (strcmp(type, "response.done") == 0) {
    log_warn("[DIRECT TURN] response done");
  } else if (strcmp(type, "conversation.item.created") == 0) {
    remember_call(event["item"].as<JsonObjectConst>());
  } else if (strcmp(type, "response.function_call_arguments.done") == 0) {
    handle_tool_done_event(event);
  } else if (strcmp(type, "session.closed") == 0) {
    s_reconnect_requested.store(true, std::memory_order_release);
  } else if (strcmp(type, "error") == 0) {
    JsonObjectConst error = event["error"].as<JsonObjectConst>();
    log_error("[DIRECT] provider error code=%s message=%s", error["code"] | "-",
              error["message"] | event["message"] | "-");
  }
}

void register_ws_handler() {
  s_ws.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    switch (type) {
      case WStype_CONNECTED:
        s_tcp_connected = true;
        s_session_create_pending = true;
        s_session_create_sent_ms = 0;
        log_warn("[DIRECT] WSS connected; creating realtime session");
        break;
      case WStype_DISCONNECTED:
        on_disconnected("wss closed");
        break;
      case WStype_ERROR:
        on_disconnected("wss error");
        break;
      case WStype_TEXT:
        handle_text_event(payload, length);
        break;
      case WStype_BIN:
        log_warn("[DIRECT] unexpected binary event len=%u", static_cast<unsigned>(length));
        break;
      default:
        break;
    }
  });
}

void start_ws() {
  String headers;
  headers.reserve(320);
  headers += "X-Api-App-Id: ";
  headers += DESKBOT_DOUBAO_APP_ID;
  headers += "\r\nX-Api-Access-Key: ";
  headers += DESKBOT_DOUBAO_ACCESS_TOKEN;
  headers += "\r\nX-Api-Resource-Id: ";
  headers += DESKBOT_DOUBAO_RESOURCE_ID;
  headers += "\r\nX-Api-App-Key: ";
  headers += DESKBOT_DOUBAO_PROTOCOL_APP_KEY;
  headers += "\r\nX-Api-Request-Id: ";
  headers += random_id("esp32-");
  s_ws.setExtraHeaders(headers.c_str());
  s_ws.setReconnectInterval(2000);
  s_last_connect_attempt_ms = millis();
#if DESKBOT_DIRECT_TLS_INSECURE
  // Test mode is still encrypted, but does not yet authenticate the server certificate.
  // The Doubao endpoint does not advertise an application subprotocol.
  s_ws.beginSSL(DESKBOT_DOUBAO_HOST, DESKBOT_DOUBAO_PORT, DESKBOT_DOUBAO_PATH, "", "");
#else
#error Install a CA certificate before disabling DESKBOT_DIRECT_TLS_INSECURE
#endif
  s_ws_started = true;
  log_warn("[DIRECT] connecting wss://%s:%u%s", DESKBOT_DOUBAO_HOST,
           static_cast<unsigned>(DESKBOT_DOUBAO_PORT), DESKBOT_DOUBAO_PATH);
}

void pump_mic() {
  if (!s_ready.load(std::memory_order_acquire) || !s_mic_ready_q || !s_mic_free_q) return;
  MicBatch* batch = nullptr;
  if (xQueueReceive(s_mic_ready_q, &batch, 0) != pdTRUE || !batch) return;
  const bool ok = send_audio_batch(*batch);
  batch->count = 0;
  (void)xQueueSend(s_mic_free_q, &batch, 0);
  if (!ok) {
    ++s_mic_drop_count;
    if (s_mic_drop_count == 1 || s_mic_drop_count % 20 == 0) {
      log_warn("[DIRECT] uplink send failed count=%u", static_cast<unsigned>(s_mic_drop_count));
    }
  }
}

void direct_task(void*) {
  for (;;) {
    const bool link_down = s_link_down.load(std::memory_order_acquire) || WiFi.status() != WL_CONNECTED;
    if (s_vision_tls_requested.load(std::memory_order_acquire)) {
      if (!s_vision_tls_granted.load(std::memory_order_acquire)) {
        s_reconnect_requested.store(false, std::memory_order_release);
        s_ws.disconnect();
        on_disconnected("vision TLS handoff");
        s_ws_started = false;
        /* stop() is synchronous, but leave one scheduler slice for the TLS
         * destructor/free path before the HTTPS worker starts allocating. */
        vTaskDelay(pdMS_TO_TICKS(30));
        s_vision_tls_granted.store(true, std::memory_order_release);
        log_warn("[DIRECT VLM] realtime TLS released internal=%u largest=%u",
                 static_cast<unsigned>(
                     heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
                 static_cast<unsigned>(
                     heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)));
      }
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    if (s_vision_tls_granted.exchange(false, std::memory_order_acq_rel)) {
      log_warn("[DIRECT VLM] HTTPS finished; reconnecting realtime");
    }
    if (s_reconnect_requested.exchange(false, std::memory_order_acq_rel)) {
      s_ws.disconnect();
      on_disconnected("reconnect requested");
    }
    if (!link_down) {
      if (!s_ws_started) start_ws();
      s_ws.loop();
      if (s_tcp_connected && s_session_create_pending && s_session_create_sent_ms == 0) {
        if (!send_session_create()) s_reconnect_requested.store(true, std::memory_order_release);
      }
      if (s_session_create_sent_ms != 0 && !s_ready.load(std::memory_order_acquire) &&
          millis() - s_session_create_sent_ms > kSessionReadyTimeoutMs) {
        log_warn("[DIRECT] session ready timeout");
        s_reconnect_requested.store(true, std::memory_order_release);
      }
      pump_mic();
      pump_playback();
      pump_vision_results();
      pump_idle_expression();
    } else {
      set_ready(false);
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

}  // namespace

bool setup_direct_realtime(void) {
#if !DESKBOT_DIRECT_CLOUD
  return false;
#else
  if (DESKBOT_DOUBAO_APP_ID[0] == '\0' || DESKBOT_DOUBAO_ACCESS_TOKEN[0] == '\0') {
    log_error("[DIRECT] missing Doubao APP ID or Access Token build flag");
    return false;
  }
  if (!psramFound()) {
    log_error("[DIRECT] PSRAM is required");
    return false;
  }
  s_mic_free_q = xQueueCreate(kMicPoolSize, sizeof(MicBatch*));
  s_mic_ready_q = xQueueCreate(kMicPoolSize, sizeof(MicBatch*));
  s_down_q = xQueueCreate(kDownQueueDepth, sizeof(DownChunk));
  s_vision_job_q = xQueueCreate(2, sizeof(VisionJob*));
  s_vision_result_q = xQueueCreate(2, sizeof(VisionResult*));
  s_mic_pool = static_cast<MicBatch*>(
      heap_caps_calloc(kMicPoolSize, sizeof(MicBatch), MALLOC_CAP_SPIRAM));
  s_audio_json = static_cast<char*>(heap_caps_malloc(kAudioJsonCapacity, MALLOC_CAP_SPIRAM));
  if (!s_mic_free_q || !s_mic_ready_q || !s_down_q || !s_vision_job_q ||
      !s_vision_result_q || !s_mic_pool || !s_audio_json) {
    log_error("[DIRECT] queue/PSRAM allocation failed");
    return false;
  }
  for (size_t i = 0; i < kMicPoolSize; ++i) {
    MicBatch* batch = &s_mic_pool[i];
    (void)xQueueSend(s_mic_free_q, &batch, 0);
  }
  register_ws_handler();
  set_ready(false);
  s_setup_ok = true;
  log_warn("[DIRECT] setup ok mic_pool=%u down_q=%u prebuffer=%ums",
           static_cast<unsigned>(kMicPoolSize), static_cast<unsigned>(kDownQueueDepth),
           static_cast<unsigned>(DESKBOT_DIRECT_AUDIO_PREBUFFER_MS));
  return true;
#endif
}

bool task_setup_direct_realtime(void) {
  if (!s_setup_ok) return false;
  if (s_task) return true;
  const BaseType_t rc = xTaskCreatePinnedToCore(direct_task, "direct_rt", kTaskStack, nullptr,
                                                kTaskPriority, &s_task, APP_CPU_NUM);
  if (rc != pdPASS) {
    log_error("[DIRECT] task create failed rc=%d internal=%u psram=%u", static_cast<int>(rc),
              static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
              static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    s_task = nullptr;
    return false;
  }
  const BaseType_t vision_rc = xTaskCreatePinnedToCore(
      vision_task, "direct_vlm", kVisionTaskStack, nullptr, 2, &s_vision_task, 0);
  if (vision_rc != pdPASS) {
    log_error("[DIRECT VLM] task create failed rc=%d internal=%u", static_cast<int>(vision_rc),
              static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)));
    s_vision_task = nullptr;
    return false;
  }
  return true;
}

bool direct_realtime_enqueue_pcm(const int16_t* samples, size_t sample_count) {
  if (!samples || sample_count == 0 || !s_mic_free_q || !s_mic_ready_q ||
      !s_ready.load(std::memory_order_acquire)) {
    if (s_mic_build && s_mic_free_q) {
      s_mic_build->count = 0;
      (void)xQueueSend(s_mic_free_q, &s_mic_build, 0);
      s_mic_build = nullptr;
    }
    return false;
  }
  size_t offset = 0;
  while (offset < sample_count) {
    if (!s_mic_build) {
      if (xQueueReceive(s_mic_free_q, &s_mic_build, 0) != pdTRUE || !s_mic_build) {
        ++s_mic_drop_count;
        return false;
      }
      s_mic_build->count = 0;
    }
    const size_t room = kMicBatchSamples - s_mic_build->count;
    const size_t copy_n = (sample_count - offset < room) ? sample_count - offset : room;
    memcpy(s_mic_build->pcm + s_mic_build->count, samples + offset, copy_n * sizeof(int16_t));
    s_mic_build->count += copy_n;
    offset += copy_n;
    if (s_mic_build->count == kMicBatchSamples) {
      MicBatch* ready = s_mic_build;
      s_mic_build = nullptr;
      if (xQueueSend(s_mic_ready_q, &ready, 0) != pdTRUE) {
        ready->count = 0;
        (void)xQueueSend(s_mic_free_q, &ready, 0);
        ++s_mic_drop_count;
        return false;
      }
    }
  }
  return true;
}

bool direct_realtime_audio_ready(void) {
  return s_ready.load(std::memory_order_acquire);
}

void direct_realtime_on_link_down(const char* why) {
  (void)why;
  s_link_down.store(true, std::memory_order_release);
  s_reconnect_requested.store(true, std::memory_order_release);
  set_ready(false);
}

void direct_realtime_on_link_up(void) {
  s_link_down.store(false, std::memory_order_release);
  s_reconnect_requested.store(true, std::memory_order_release);
}

void direct_realtime_show_idle(void) {
  render_face("idle", true);
}
