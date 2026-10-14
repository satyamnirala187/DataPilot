import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import Logo from './Logo.jsx'

describe('Logo', () => {
  it('shows the approved logo file as an image named DataPilot', () => {
    render(<Logo className="sidebar-logo" />)
    const logo = screen.getByRole('img', { name: 'DataPilot' })
    expect(logo.getAttribute('src')).toMatch(/datapilot-logo-color.*\.svg/)
    expect(logo).toHaveClass('sidebar-logo')
  })

  it('can be decorative when the name is already announced nearby', () => {
    const { container } = render(<Logo alt="" />)
    expect(screen.queryByRole('img', { name: 'DataPilot' })).toBeNull()
    expect(container.querySelector('img')).toHaveAttribute('alt', '')
  })
})
