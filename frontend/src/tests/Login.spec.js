import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import Login from '../components/Login.vue'
import axios from 'axios'

// Mock axios
vi.mock('axios', () => {
  const mockAxios = {
    get: vi.fn().mockResolvedValue({ data: { setup_required: false } }),
    post: vi.fn(),
    defaults: {
      headers: {
        common: {}
      }
    }
  }
  return { default: mockAxios }
})

// Mock useRouter
const mockPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: mockPush
  })
}))

const stubs = {
  'v-container': { template: '<div><slot /></div>' },
  'v-row': { template: '<div><slot /></div>' },
  'v-col': { template: '<div><slot /></div>' },
  'v-card': { template: '<div><slot /></div>' },
  'v-card-title': { template: '<div class="v-card-title"><slot /></div>' },
  'v-card-text': { template: '<div><slot /></div>' },
  'v-form': { template: '<form @submit="$emit(\'submit\', $event)"><slot /></form>' },
  'v-text-field': {
    props: ['modelValue'],
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  },
  'v-btn': { template: '<button type="submit"><slot /></button>' },
  'v-icon': { template: '<i></i>' },
  'v-alert': { template: '<div class="v-alert"><slot /></div>' }
}

describe('Login.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    axios.get.mockResolvedValue({ data: { setup_required: false } })
  })

  it('renders login form', () => {
    const wrapper = mount(Login, { global: { stubs } })
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.v-card-title').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.findAll('input').length).toBeGreaterThanOrEqual(2)
  })

  it('displays network connection error when backend is unreachable', async () => {
    axios.post.mockRejectedValue(new Error('Network Error'))
    const wrapper = mount(Login, { global: { stubs } })

    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('admin')
    await inputs[1].setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.v-alert').exists()).toBe(true)
    expect(wrapper.find('.v-alert').text()).toContain('Cannot connect to backend server')
  })

  it('displays 500 server error message when backend throws 500', async () => {
    const err = new Error('Internal Server Error')
    err.response = { status: 500, data: {} }
    axios.post.mockRejectedValue(err)

    const wrapper = mount(Login, { global: { stubs } })
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('admin')
    await inputs[1].setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.v-alert').exists()).toBe(true)
    expect(wrapper.find('.v-alert').text()).toContain('Backend server error (500)')
  })

  it('displays invalid credentials message on 401', async () => {
    const err = new Error('Unauthorized')
    err.response = { status: 401, data: { detail: 'Invalid username or password' } }
    axios.post.mockRejectedValue(err)

    const wrapper = mount(Login, { global: { stubs } })
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('admin')
    await inputs[1].setValue('wrong')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.v-alert').exists()).toBe(true)
    expect(wrapper.find('.v-alert').text()).toContain('Invalid username or password')
  })
})
