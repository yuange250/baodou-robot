#pragma once

/*
 * Copy this file to deskbot_local_config.h and fill in local values.
 * deskbot_local_config.h is ignored by Git and must never be committed.
 */

#define WIFI_DEFAULT_SSID "your-wifi-name"
#define WIFI_DEFAULT_PASSWORD "your-wifi-password"

/* Only needed when using the self-hosted service path. */
#define DESKBOT_WS_HOST "192.168.1.100"
#define DESKBOT_WS_PORT 9000
#define DESKBOT_API_KEY "your-device-api-key"

/* 1 = ESP32 connects directly to Doubao Realtime; 0 = self-hosted service. */
#ifndef DESKBOT_DIRECT_CLOUD
#define DESKBOT_DIRECT_CLOUD 1
#endif

/* Volcengine credentials are read from the ignored service/.env by
 * hardware/scripts/build_direct.ps1, not stored in this header. */
