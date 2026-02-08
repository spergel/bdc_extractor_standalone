/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        ink: '#101214',
        panel: '#1b1f23',
        accent: '#d8a31a',
        teal: '#0f4d4a',
        danger: '#c23a2b',
        silver: '#bfc6c6',
      },
      borderRadius: {
        window: '12px',
      },
      boxShadow: {
        crt: '0 0 0 1px #2a2e34 inset, 0 8px 24px rgba(0,0,0,0.4)',
      },
    },
  },
  plugins: [],
};












































