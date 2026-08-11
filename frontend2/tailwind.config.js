/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg:       '#09090f',
        bg2:      '#0f0f1a',
        surface:  '#13131f',
        surface2: '#1a1a2a',
        gold:     '#f0b429',
        gold2:    '#ffd166',
        win:      '#2ecc71',
        loss:     '#e74c3c',
        blue:     '#4fc3f7',
        purple:   '#b39ddb',
        border:   'rgba(255,255,255,0.07)',
        border2:  'rgba(255,255,255,0.12)',
        t1:       '#e8e8f0',
        t2:       '#9090a8',
        t3:       '#9898b0',
        // Aspect colors
        'asp-heroism':    '#c6c1a0',
        'asp-villainy':   '#8b3a6b',
        'asp-cunning':    '#f0b429',
        'asp-aggression': '#ff7043',
        'asp-command':    '#2ecc71',
        'asp-vigilance':  '#4fc3f7',
        'asp-force':      '#b39ddb',
        // Tier colors
        'tier-s': '#f72585',
        'tier-a': '#4cc9f0',
        'tier-b': '#2ecc71',
        'tier-c': '#f0b429',
        'tier-d': '#ff9f1c',
        'tier-f': '#e74c3c',
      },
      fontFamily: {
        display: ['Rajdhani', 'sans-serif'],
        mono:    ['"Share Tech Mono"', 'monospace'],
        body:    ['Barlow', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
