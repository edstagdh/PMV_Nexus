# Web interface (Vue 3)

Source of the app's interface. The built files live in `../frontend` and are
served by the app at `/`, so **running the app never needs Node.js** —
only changing this source does.

```bash
cd ui
npm install        # once
npm run build      # replaces ../frontend (index.html + assets/)
npm run dev        # hot-reload dev server on http://localhost:5173/ (app must run on :8676)
```

Layout: `src/views` (pages; `views/admin` for the Admin section), `src/components`
(building blocks; `components/admin` for admin-only panels), `src/stores`
(session, live updates, toasts), `src/lib` (API client, formatting),
`src/styles` (themes and base styles).

Pages that need a permission declare it in `src/router.js` (`meta.admin` or
`meta.permission`); the server checks every request again.
