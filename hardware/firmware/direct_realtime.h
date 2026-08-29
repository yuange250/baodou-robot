#pragma once

#include <stddef.h>
#include <stdint.h>

/** Initialize the ESP32 -> Doubao Realtime direct-cloud transport. */
bool setup_direct_realtime(void);

/** Start the single-owner TLS/WebSocket task. */
bool task_setup_direct_realtime(void);

/** Copy PCM16 mono microphone samples into the direct-cloud uplink queue. */
bool direct_realtime_enqueue_pcm(const int16_t* samples, size_t sample_count);

/** True after the provider has acknowledged session.create. */
bool direct_realtime_audio_ready(void);

/** Wi-Fi callbacks only set flags; the direct-cloud task owns the socket. */
void direct_realtime_on_link_down(const char* why = nullptr);
void direct_realtime_on_link_up(void);

/** Render the built-in idle face used when no server supplies PB animation. */
void direct_realtime_show_idle(void);
