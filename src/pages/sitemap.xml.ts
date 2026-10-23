import { statSync } from 'node:fs';

// lastmod: the home page is rebuilt with the live reading, so it is today; about changes only when its source does
// (git archive stamps files with their commit time, so the mtime is right on the server too)
const day = (d: Date) => d.toISOString().slice(0, 10);
export const GET = ({ site }: { site: URL }) => {
  const pages = [
    ['/', day(new Date())],
    ['/about/', day(statSync('src/pages/about.astro').mtime)],
  ];
  const urls = pages.map(([p, m]) => `  <url><loc>${new URL(p, site)}</loc><lastmod>${m}</lastmod></url>`).join('\n');
  return new Response(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`, {
    headers: { 'Content-Type': 'application/xml' },
  });
};
