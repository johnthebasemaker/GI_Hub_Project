import { Thumb, useThumbs } from './thumbs'

/** Phase 23d — the ⌘K results' pictures, loaded only when the palette shows
 * materials (kept off the sign-in page's critical path). */
export default function PaletteThumb({ sap, all }: { sap: string; all: string[] }) {
  const { data } = useThumbs({ saps: all })
  return <Thumb src={data?.saps[sap]} size={24} />
}
