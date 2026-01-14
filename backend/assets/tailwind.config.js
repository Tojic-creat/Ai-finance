// backend/assets/tailwind.config.js
//
// Tailwind configuration tuned for the FinAssist project.
// - Content: Django templates + any frontend/src files under backend/templates/static.
// - Dark mode: class-based (so we can toggle with <html class="dark">).
// - Extends: brand palette, spacing, borderRadius, fontFamily, container settings.
// - Useful plugins: @tailwindcss/forms, @tailwindcss/typography, @tailwindcss/aspect-ratio
//
// Usage (dev):
//   npx tailwindcss -i ./backend/templates/static/src/input.css -o ./backend/templates/static/css/tailwind.css --watch
//
// Note: adjust paths if your static build pipeline differs.

const defaultTheme = require("tailwindcss/defaultTheme");

module.exports = {
  // class-based dark mode (use <html class="dark">)
  darkMode: "class",

  // где искать шаблоны для генерации CSS (Django templates + static source files)
  content: [
    "./backend/templates/**/*.html",
    "./backend/apps/**/templates/**/*.html",
    "./backend/templates/static/src/**/*.{js,jsx,ts,tsx,css,scss}",
    // if you use any frontend packages that output html/js into other folders, add them here
  ],

  theme: {
    extend: {
      // контейнер по центру с разумными отступами
      container: {
        center: true,
        padding: {
          DEFAULT: "1rem",
          sm: "1.5rem",
          lg: "2rem",
          xl: "3rem",
        },
      },

      // шрифты: наследуем системные и добавляем санс-сериф
      fontFamily: {
        sans: ["Inter", ...defaultTheme.fontFamily.sans],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "monospace",
        ],
      },

      // палитра бренда (настройте по вкусу)
      colors: {
        brand: {
          50: "#f5fbff",
          100: "#e6f6ff",
          200: "#bfefff",
          300: "#99e6ff",
          400: "#4fd6ff",
          500: "#19c7ff",
          600: "#11a6cc",
          700: "#0b7788",
          800: "#064f55",
          900: "#023237",
        },
        // полезные нейтралы для тёмной/светлой темы
        neutral: {
          50: "#fafafa",
          100: "#f4f4f5",
          200: "#e9e9eb",
          300: "#d6d6db",
          400: "#9fa0a6",
          500: "#6f7075",
          600: "#4b4b50",
          700: "#2f2f33",
          800: "#1b1b1d",
          900: "#0a0a0b",
        },
      },

      // дополнительные размеры / радиусы
      spacing: {
        18: "4.5rem",
        22: "5.5rem",
        26: "6.5rem",
      },
      borderRadius: {
        xl: "1rem",
      },

      // тени для карточек
      boxShadow: {
        "card-sm": "0 1px 3px rgba(15, 23, 42, 0.04)",
        "card-md": "0 6px 18px rgba(15, 23, 42, 0.08)",
      },

      // плавные переходы
      transitionTimingFunction: {
        "in-out-quad": "cubic-bezier(.4,0,.2,1)",
      },
    },
  },

  variants: {
    extend: {
      backgroundColor: ["active", "checked", "group-hover"],
      borderColor: ["focus-visible", "first"],
      opacity: ["disabled"],
      transform: ["hover", "focus"],
    },
  },

  plugins: [
    require("@tailwindcss/forms")({
      strategy: "class", // use form classes (helps avoid global form style changes)
    }),
    require("@tailwindcss/typography"),
    require("@tailwindcss/aspect-ratio"),
    // Add other plugins if needed (e.g. line-clamp) by installing and uncommenting:
    // require('@tailwindcss/line-clamp'),
  ],
};
