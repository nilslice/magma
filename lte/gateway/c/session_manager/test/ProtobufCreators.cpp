/**
 * Copyright (c) 2016-present, Facebook, Inc.
 * All rights reserved.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree. An additional grant
 * of patent rights can be found in the PATENTS file in the same directory.
 */

#include "ProtobufCreators.h"

namespace magma {

void create_rule_record(
    const std::string& imsi,
    const std::string& rule_id,
    uint64_t bytes_rx,
    uint64_t bytes_tx,
    RuleRecord* rule_record) {
  rule_record->set_sid(imsi);
  rule_record->set_rule_id(rule_id);
  rule_record->set_bytes_rx(bytes_rx);
  rule_record->set_bytes_tx(bytes_tx);
}

void create_charging_credit(
    uint64_t volume,
    bool is_final,
    ChargingCredit* credit) {
  credit->mutable_granted_units()->mutable_total()->set_volume(volume);
  credit->mutable_granted_units()->mutable_total()->set_is_valid(true);
  credit->set_type(ChargingCredit::BYTES);
  credit->set_is_final(is_final);
}

// defaults to not final credit
void create_update_response(
    const std::string& imsi,
    uint32_t charging_key,
    uint64_t volume,
    CreditUpdateResponse* response) {
  create_update_response(imsi, charging_key, volume, false, response);
}

void create_update_response(
    const std::string& imsi,
    uint32_t charging_key,
    uint64_t volume,
    bool is_final,
    CreditUpdateResponse* response) {
  create_charging_credit(volume, is_final, response->mutable_credit());
  response->set_success(true);
  response->set_sid(imsi);
  response->set_charging_key(charging_key);
  response->set_type(CreditUpdateResponse::UPDATE);
}

void create_usage_update(
    const std::string& imsi,
    uint32_t charging_key,
    uint64_t bytes_rx,
    uint64_t bytes_tx,
    CreditUsage::UpdateType type,
    CreditUsageUpdate* update) {
  auto usage = update->mutable_usage();
  update->set_sid(imsi);
  usage->set_charging_key(charging_key);
  usage->set_bytes_rx(bytes_rx);
  usage->set_bytes_tx(bytes_tx);
  usage->set_type(type);
}

void create_monitor_credit(
    const std::string& m_key,
    MonitoringLevel level,
    uint64_t volume,
    UsageMonitoringCredit* credit) {
  if (volume == 0) {
    credit->set_action(UsageMonitoringCredit::DISABLE);
  } else {
    credit->set_action(UsageMonitoringCredit::CONTINUE);
  }
  credit->mutable_granted_units()->mutable_total()->set_volume(volume);
  credit->mutable_granted_units()->mutable_total()->set_is_valid(true);
  credit->set_level(level);
  credit->set_monitoring_key(m_key);
}

void create_monitor_update_response(
    const std::string& imsi,
    const std::string& m_key,
    MonitoringLevel level,
    uint64_t volume,
    UsageMonitoringUpdateResponse* response) {
  create_monitor_credit(m_key, level, volume, response->mutable_credit());
  response->set_success(true);
  response->set_sid(imsi);
}

}
