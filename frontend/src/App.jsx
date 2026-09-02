/**
 * Application shell.
 *
 * Owns the page frame (header, main, footer) and renders the current page.
 * There is exactly one page today, so no router is pulled in yet - adding
 * react-router later means replacing the <HomePage /> line and nothing else.
 */
import Header from './components/Header.jsx'
import Footer from './components/Footer.jsx'
import HomePage from './pages/HomePage.jsx'

export default function App() {
  return (
    <div className="flex min-h-full flex-col">
      <Header />
      <main className="flex-1">
        <HomePage />
      </main>
      <Footer />
    </div>
  )
}
