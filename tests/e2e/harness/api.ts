/**
 * API-side helpers for specs: read the access token a role's storageState was
 * minted with, and build a Playwright request context that talks DIRECTLY to
 * the hermetic backend (bypassing the Vite proxy) as that role.
 */
import { APIRequestContext, request } from '@playwright/test'
import * as fs from 'node:fs'
import { API_URL, Role, apiHeaders, storageStatePath } from './env'

export function tokenFor(role: Role): string {
  const state = JSON.parse(fs.readFileSync(storageStatePath(role), 'utf-8')) as {
    origins: { localStorage: { name: string; value: string }[] }[]
  }
  const entry = state.origins[0]?.localStorage.find((e) => e.name === 'gi_token')
  if (!entry) throw new Error(`no gi_token in storage state for ${role} — did the setup project run?`)
  return entry.value
}

export async function apiAs(role: Role, ipBucket = '77'): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: API_URL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${tokenFor(role)}`,
      ...apiHeaders(ipBucket),
    },
  })
}
