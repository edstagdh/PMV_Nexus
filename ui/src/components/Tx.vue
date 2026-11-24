<script>
// A translated sentence with markup inside it (3.7.0): the whole sentence is one text, so
// a language can put the parts in its own order. {name} placeholders are filled from
// slots of the same name (bold text, links, chips…) or from `params` (plain text).
//   <Tx msg="The job {job} is turned off" :params="{}"><template #job><strong>…</strong></template></Tx>
//   <Tx msg="{n} file, {size}" plural="{n} files, {size}" :n="count">…</Tx>   (plural forms, like tn())
// Only `msg` is translated; slot content is the page's own markup, never from a language file.
import { h } from "vue";
import { t, tnRaw, formatNumber } from "../lib/i18n.js";

const PART = /\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g;

export default {
  name: "Tx",  // i18n-ignore
  props: {
    msg: { type: String, required: true }, plural: { type: String, default: "" }, n: { type: Number, default: 0 },
    params: { type: Object, default: null }, tag: { type: String, default: "span" },
  },
  setup(props, { slots }) {
    return () => {
      const text = props.plural ? tnRaw(props.msg, props.plural, props.n) : t(props.msg);
      const params = props.plural ? { n: formatNumber(props.n), ...props.params } : props.params;
      const out = [];
      let last = 0;
      for (const m of text.matchAll(PART)) {
        if (m.index > last) out.push(text.slice(last, m.index));
        const name = m[1];
        if (slots[name]) out.push(...slots[name]());
        else if (params && params[name] != null) out.push(String(params[name]));
        else out.push(m[0]);
        last = m.index + m[0].length;
      }
      if (last < text.length) out.push(text.slice(last));
      return h(props.tag, null, out);  // i18n-ignore
    };
  },
};
</script>
