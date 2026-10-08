/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      keyframes: {
        'march-x': { to: { backgroundPosition: '16px 0' } },
        'arrow-travel-x': {
          '0%, 100%': { transform: 'translateX(0)', opacity: '0.4' },
          '50%': { transform: 'translateX(6px)', opacity: '1' },
        },
        dwStageFlash: {
          '0%': { backgroundColor: 'transparent' },
          '50%': { backgroundColor: '#fef3c7' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
      animation: {
        'march-x': 'march-x 0.5s linear infinite',
        'arrow-travel-x': 'arrow-travel-x 1s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
