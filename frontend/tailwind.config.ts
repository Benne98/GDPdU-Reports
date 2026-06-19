import type { Config } from 'tailwindcss'
import typography from '@tailwindcss/typography'

const config: Config = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Page backgrounds
        surface: {
          DEFAULT: '#F4F6F9',   // main page bg
          alt:     '#EEF1F6',   // alternate section bg
          white:   '#FFFFFF',   // card surface
        },
        // Alias for existing GDPdU components (same scale as primary)
        brand: {
          DEFAULT: '#1E3A5F',
          50:  '#EEF3FA',
          100: '#C8D9EE',
          200: '#91B2DC',
          300: '#5A8BCB',
          400: '#2E6AAD',
          500: '#1E3A5F',
          600: '#17304F',
          700: '#10253E',
          800: '#091A2D',
          accent: '#3B82F6',
        },
        // Primary brand colour: deep slate blue
        primary: {
          DEFAULT: '#1E3A5F',
          50:  '#EEF3FA',
          100: '#C8D9EE',
          200: '#91B2DC',
          300: '#5A8BCB',
          400: '#2E6AAD',
          500: '#1E3A5F',
          600: '#17304F',
          700: '#10253E',
          800: '#091A2D',
          900: '#040E1C',
        },
        // Borders & dividers
        border: {
          DEFAULT: '#CBD5E1',
          subtle:  '#E2E8F0',
          strong:  '#94A3B8',
        },
        // Text
        text: {
          primary:   '#111827',
          secondary: '#475569',
          muted:     '#94A3B8',
        },
        // Legacy aliases kept for any remaining references
        navy: {
          DEFAULT: '#1E3A5F',
          800:     '#F4F6F9',
        },
        gold: {
          DEFAULT: '#1E3A5F',
          light:   '#2E5280',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        serif: ['Newsreader', 'Georgia', 'Times New Roman', 'serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }],
      },
      backgroundImage: {
        'grid-surface': `linear-gradient(rgba(30,58,95,0.04) 1px, transparent 1px),
                         linear-gradient(to right, rgba(30,58,95,0.04) 1px, transparent 1px)`,
        'hero-gradient': 'radial-gradient(ellipse 80% 60% at 50% -10%, rgba(30,58,95,0.08) 0%, transparent 70%)',
        'card-gradient': 'linear-gradient(135deg, #FFFFFF 0%, #F8FAFC 100%)',
        'primary-gradient': 'linear-gradient(135deg, #1E3A5F 0%, #2E5280 50%, #1E3A5F 100%)',
      },
      backgroundSize: {
        grid: '40px 40px',
      },
      boxShadow: {
        card:        '0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.06)',
        'card-hover':'0 4px 24px rgba(30,58,95,0.14), 0 0 0 1px rgba(30,58,95,0.12)',
        primary:     '0 0 20px rgba(30,58,95,0.2)',
        'primary-sm':'0 0 8px rgba(30,58,95,0.15)',
      },
      animation: {
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in':    'fadeIn 0.6s ease-out forwards',
        'slide-up':   'slideUp 0.6s ease-out forwards',
      },
      keyframes: {
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%':   { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [typography],
}

export default config
