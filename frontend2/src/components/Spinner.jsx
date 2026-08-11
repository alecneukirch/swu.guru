export default function Spinner({ className = '' }) {
  return (
    <div className={`flex items-center justify-center py-16 ${className}`}>
      <div className="w-8 h-8 border-2 border-border2 border-t-gold rounded-full animate-spin" />
    </div>
  )
}
