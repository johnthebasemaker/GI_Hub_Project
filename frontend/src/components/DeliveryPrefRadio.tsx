import { useState } from 'react'
import { Radio, Space, Tooltip, Typography } from 'antd'
import { FieldTimeOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { getDeliveryPreference, setDeliveryPreference } from '../api/client'

/**
 * WhatsApp delivery preference for material-transaction forms (issue /
 * receive / return). "Urgent" sends workflow alerts immediately; "Evening"
 * batches them into the 16:00 digest. The choice is sticky (localStorage)
 * across the three entry pages and travels as the X-Delivery-Preference
 * header ONLY on transaction posts — profile/OTP calls are always immediate.
 * Critical alerts ignore this and always send at once.
 */
export default function DeliveryPrefRadio() {
  const [pref, setPref] = useState<'urgent' | 'evening'>(getDeliveryPreference())
  return (
    <Space size={8} wrap>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        WhatsApp alerts:
      </Typography.Text>
      <Radio.Group
        size="small"
        optionType="button"
        buttonStyle="solid"
        value={pref}
        onChange={(e) => {
          const v = e.target.value as 'urgent' | 'evening'
          setPref(v)
          setDeliveryPreference(v)
        }}
      >
        <Tooltip title="Notifications for this entry go out immediately">
          <Radio.Button value="urgent"><ThunderboltOutlined /> Urgent</Radio.Button>
        </Tooltip>
        <Tooltip title="Non-critical notifications are batched into one 16:00 evening digest">
          <Radio.Button value="evening"><FieldTimeOutlined /> Evening digest</Radio.Button>
        </Tooltip>
      </Radio.Group>
    </Space>
  )
}
