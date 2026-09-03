/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: '#030712',
          900: '#070c18',
          800: '#0d1527',
          700: '#152038',
        },
        cyber: {
          cyan: '#00f0ff',
          emerald: '#10b981',
          rose: '#ff2a6d',
          amber: '#ffb703',
          violet: '#7000ff',
          blue: '#3b82f6',
        }
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['Fira Code', 'monospace'],
        display: ['Chakra Petch', 'sans-serif'],
      },
      animation: {
        'pulse-glow': 'pulseGlow 2.5s infinite ease-in-out',
        'radar-spin': 'radarSpin 4s linear infinite',
        'marquee': 'marquee 25s linear infinite',
        'strobe-red': 'strobeRed 1s infinite alternate',
        'scanline-anim': 'scanlineScroll 8s linear infinite',
      },
      keyframes: {
        pulseGlow: {
          '0%, 100%': { opacity: '0.4', filter: 'drop-shadow(0 0 5px rgba(0, 240, 255, 0.4))' },
          '50%': { opacity: '1', filter: 'drop-shadow(0 0 15px rgba(0, 240, 255, 0.8))' },
        },
        radarSpin: {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
        marquee: {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(-100%)' },
        },
        strobeRed: {
          '0%': { borderColor: 'rgba(239, 68, 68, 0.3)', boxShadow: '0 0 10px rgba(239, 68, 68, 0.2)' },
          '100%': { borderColor: 'rgba(239, 68, 68, 1)', boxShadow: '0 0 30px rgba(239, 68, 68, 0.8)' },
        },
        scanlineScroll: {
          '0%': { backgroundPosition: '0 0' },
          '100%': { backgroundPosition: '0 100%' },
        }
      }
    },
  },
  plugins: [],
}

