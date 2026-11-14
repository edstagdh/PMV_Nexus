// Ported from the classic interface (frontend/js/markdown.js), which goes away
// with it. Minimal, dependency-free Markdown -> HTML renderer for the in-app Guide,
// Changelog and About pages. Covers what those documents use: headings,
// paragraphs, bold/italic, inline code, fenced code, links, ordered/
// unordered (nested) lists, blockquotes, horizontal rules, pipe tables and the
// Guide's own pictures (![text](screenshots/name.webp), 3.2.0 -- nothing else).
// All text is HTML-escaped first; links are restricted to http(s), mailto,
// relative and #anchor targets.
const escapeHtml = (str) => String(str)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

function slugify(text) {
  return text.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");
}

function inline(text) {
  const codes = [];
  let out = escapeHtml(text).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out
    .replace(/!\[([^\]]*)\]\(screenshots\/([a-z0-9][a-z0-9-]*\.(?:webp|png))\)/g, (m, alt, name) =>
      `<img class="doc-shot" src="/api/system/docs/screenshots/${name}" alt="${alt}" loading="lazy">`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) => {
      const decoded = href.replace(/&amp;/g, "&");
      if (!/^(https?:\/\/|mailto:|\/|#|\.\/)/i.test(decoded)) return label;
      const external = /^https?:\/\//i.test(decoded);
      return `<a href="${href}"${external ? ' target="_blank" rel="noopener noreferrer"' : ""}>${label}</a>`;
    })
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

function renderList(lines) {
  // lines: [{indent, ordered, text}] -> nested <ul>/<ol>
  let html = "";
  const stack = [];
  for (const item of lines) {
    while (stack.length && item.indent < stack[stack.length - 1].indent) {
      html += `</li></${stack.pop().tag}>`;
    }
    let top = stack[stack.length - 1];
    const tag = item.ordered ? "ol" : "ul";
    if (top && item.indent === top.indent && top.tag !== tag) {
      html += `</li></${stack.pop().tag}>`; // list type changed: start a new list
      top = stack[stack.length - 1];
    }
    if (!top || item.indent > top.indent) {
      stack.push({ indent: item.indent, tag });
      html += `<${tag}><li>${inline(item.text)}`;
    } else {
      html += `</li><li>${inline(item.text)}`;
    }
  }
  while (stack.length) html += `</li></${stack.pop().tag}>`;
  return html;
}

function splitRow(row) {
  return row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

export function renderMarkdown(md) {
  const lines = (md || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*$/.test(line)) { i++; continue; }

    const fence = line.match(/^```(\w*)/);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(`<pre><code>${escapeHtml(body.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const html = inline(heading[2]);
      out.push(`<h${level} id="${slugify(html)}">${html}</h${level}>`);
      i++;
      continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    if (/^>\s?/.test(line)) {
      const body = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${renderMarkdown(body.join("\n"))}</blockquote>`);
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(splitRow(lines[i++]));
      out.push(
        `<div class="md-table-wrap"><table><thead><tr>${head.map((h) => `<th>${inline(h)}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
      );
      continue;
    }

    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (m) {
          items.push({ indent: m[1].replace(/\t/g, "  ").length, ordered: /\d/.test(m[2]), text: m[3] });
          i++;
        } else if (/^\s{2,}\S/.test(lines[i]) && items.length) {
          items[items.length - 1].text += " " + lines[i].trim();
          i++;
        } else break;
      }
      out.push(renderList(items));
      continue;
    }

    const para = [];
    while (
      i < lines.length && !/^\s*$/.test(lines[i]) &&
      !/^(#{1,6}\s|```|>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i])
    ) para.push(lines[i++].trim());
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}
