// Entity metadata driving the generic table + CRUD screens. Mirrors the backend
// (backend/api/main.py ENTITIES). Read entities are browse-only; write entities
// are the master-data tables the API exposes POST/PUT/DELETE for.

// Access rule per read entity — mirrors the legacy visibility of each log.
// Shape matches AccessRule in nav.tsx (kept structural to avoid a circular import).
export type EntityAccess = { anyRole: string[] } | { minLevel: number }

export interface ReadEntity {
  key: string
  label: string
  path: string
  hasSite: boolean
  access: EntityAccess
}

export interface Field {
  name: string
  label: string
  required?: boolean
  type?: 'text' | 'select'
  options?: string[]
}

export interface WriteEntity {
  key: string
  label: string
  path: string
  idKey: string
  fields: Field[]
}

export const READ_ENTITIES: ReadEntity[] = [
  // inventory (stock list) is a benign, site-scoped read → all roles.
  { key: 'inventory', label: 'Inventory', path: '/inventory', hasSite: true, access: { minLevel: 0 } },
  // ledger logs are an oversight surface → hod+ (SK reviews its own entries in
  // the Data Entry staging grid, not here).
  { key: 'receipts', label: 'Receipts', path: '/receipts', hasSite: true, access: { minLevel: 2 } },
  { key: 'consumption', label: 'Consumption', path: '/consumption', hasSite: true, access: { minLevel: 2 } },
  { key: 'returns', label: 'Returns', path: '/returns', hasSite: true, access: { minLevel: 2 } },
  { key: 'lots', label: 'Lots', path: '/lots', hasSite: true, access: { minLevel: 2 } },
  { key: 'purchase-orders', label: 'Purchase Orders', path: '/purchase-orders', hasSite: true, access: { minLevel: 3 } },
  { key: 'equipment', label: 'Equipment (SME)', path: '/equipment', hasSite: true, access: { anyRole: ['hod'] } },
]

const STATUS: Field = {
  name: 'status',
  label: 'Status',
  type: 'select',
  options: ['active', 'inactive'],
}

export const WRITE_ENTITIES: WriteEntity[] = [
  {
    key: 'vendors',
    label: 'Vendors',
    path: '/vendors',
    idKey: 'id',
    fields: [
      { name: 'Vendor_Code', label: 'Vendor Code', required: true },
      { name: 'Vendor_Name', label: 'Vendor Name', required: true },
      { name: 'Address', label: 'Address' },
      { name: 'Contact_Name', label: 'Contact Name' },
      { name: 'Contact_Phone', label: 'Contact Phone' },
      { name: 'Contact_Email', label: 'Contact Email' },
      STATUS,
    ],
  },
  {
    key: 'warehouses',
    label: 'Warehouses',
    path: '/warehouses',
    idKey: 'id',
    fields: [
      { name: 'Warehouse_ID', label: 'Warehouse ID', required: true },
      { name: 'Name', label: 'Name', required: true },
      { name: 'Location', label: 'Location' },
      { name: 'Contact_Name', label: 'Contact Name' },
      { name: 'Contact_Phone', label: 'Contact Phone' },
      { name: 'Contact_Email', label: 'Contact Email' },
      STATUS,
    ],
  },
  {
    key: 'employees',
    label: 'Employees',
    path: '/employees',
    idKey: 'id',
    fields: [
      { name: 'ID_Number', label: 'ID Number', required: true },
      { name: 'Name', label: 'Name', required: true },
      { name: 'Phone_Number', label: 'Phone Number' },
      { name: 'Department', label: 'Department' },
      { name: 'Site_ID', label: 'Site' },
      STATUS,
    ],
  },
]
