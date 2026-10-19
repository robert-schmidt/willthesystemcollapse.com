import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://willthesystemcollapse.com',
  // the CSS is small (~7 KB gzipped): inline it so the first paint doesn't wait on extra requests
  build: { inlineStylesheets: 'always' },
});
