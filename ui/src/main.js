import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router.js";
import "./styles/theme.css";
import "./styles/base.css";

createApp(App).use(router).mount("#app");
