// backend/templates/static/assets/postcss.config.js
//
// PostCSS configuration for FinAssist frontend assets.
// Uses Tailwind CSS and Autoprefixer. In production, also runs cssnano to minify output.
//
// Install required deps (dev):
//   npm install -D tailwindcss autoprefixer cssnano
//
// `NODE_ENV=production` will enable cssnano automatically.

module.exports = {
  plugins: {
    // Tailwind CSS - required for utility generation
    tailwindcss: {},
    // Autoprefixer - add vendor prefixes automatically
    autoprefixer: {},
    // cssnano - minify in production for smaller builds
    ...(process.env.NODE_ENV === "production"
      ? { cssnano: { preset: "default" } }
      : {}),
  },
};
