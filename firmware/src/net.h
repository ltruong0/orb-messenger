#pragma once
// net_task (core 0): Wi-Fi (or the setup portal), SNTP, MQTT with LWT, media
// downloads and sticker sync. Talks to the UI only through ui_queue/net_queue.
void net_start();
