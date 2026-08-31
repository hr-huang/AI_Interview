import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom/vitest'
import { App } from './App'

test('renders enterprise product identity and primary assessment route', () => {
  render(
    <MemoryRouter initialEntries={['/assessments/new']}>
      <App />
    </MemoryRouter>,
  )

  expect(screen.getByText('衡鉴 · Evidence Hiring')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '创建岗位胜任力评估' })).toBeInTheDocument()
})
