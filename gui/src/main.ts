import { createApp } from "vue";
import { createPinia } from "pinia";
import { trackEvent } from "@aptabase/tauri";
import App from "./App.vue";

void trackEvent("app_started").catch((error: unknown) => {
  console.warn("Unable to send Aptabase app_started event:", error);
});

createApp(App).use(createPinia()).mount("#app");
