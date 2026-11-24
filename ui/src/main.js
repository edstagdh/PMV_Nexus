import { createApp } from "vue";
import App from "./App.vue";
import Tx from "./components/Tx.vue";
import { router } from "./router.js";
import { session } from "./stores/session.js";
import { t, tn, setLanguage, pickLanguage } from "./lib/i18n.js";
import "./styles/theme.css";
import "./styles/base.css";

// The language loads before the first paint, so nobody sees English flash first (3.7.0).
async function start() {
  const code = await pickLanguage(session.prefs.language, { signIn: !session.prefs.language });
  await setLanguage(code);
  const app = createApp(App).use(router);
  app.config.globalProperties.$t = t;
  app.config.globalProperties.$tn = tn;
  app.component("Tx", Tx);  // i18n-ignore
  app.mount("#app");
}
start();
