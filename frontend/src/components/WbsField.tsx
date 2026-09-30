import { Col, Form, Select } from 'antd'

/**
 * The WBS Number on Receive and Issue.
 *
 * WHO OWNS THE LIST: the site's HOD, on HOD → WBS (`wbs_master`). Logistics
 * does not assign one. With a list, the field is REQUIRED (the server's
 * `assert_wbs` gate refuses an entry without one); on Issue a work type the
 * HOD mapped to a WBS fills it in when left blank (`services/wbs.resolve_wbs`).
 *
 * 2026-09-30: with NO list the field used to be absent, so nobody could tell
 * the feature existed — Live CNCEC had zero WBS numbers and the operator asked
 * where the WBS selection was. It now shows, disabled, saying who adds them.
 * Still not required: the server gate is conditional on the list existing.
 */
export default function WbsField({ options, site }: { options?: string[]; site?: string }) {
  if (options?.length) {
    return (
      <Col xs={24} md={8}>
        <Form.Item name="wbs" label="WBS Number"
          rules={[{ required: true, message: 'This site requires a WBS' }]}>
          <Select showSearch placeholder="Pick WBS"
            options={options.map((w) => ({ value: w, label: w }))} />
        </Form.Item>
      </Col>
    )
  }
  if (!site || options === undefined) return null
  return (
    <Col xs={24} md={8}>
      <Form.Item label="WBS Number"
        extra="No WBS numbers for this site yet — the HOD adds them under HOD → WBS, then you pick one here.">
        <Select disabled placeholder="None set up" data-testid="wbs-none" />
      </Form.Item>
    </Col>
  )
}
