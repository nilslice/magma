"""
Copyright (c) 2016-present, Facebook, Inc.
All rights reserved.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree. An additional grant
of patent rights can be found in the PATENTS file in the same directory.
"""

import logging
from collections import namedtuple
from typing import Any
from abc import ABC, abstractmethod
from magma.enodebd.data_models.data_model_parameters import ParameterName
from magma.enodebd.device_config.configuration_init import build_desired_config
from magma.enodebd.enodeb_status import get_enodeb_status, \
    update_status_metrics
from magma.enodebd.exceptions import ConfigurationError, Tr069Error
from magma.enodebd.state_machines.acs_state_utils import \
    process_inform_message, get_all_objects_to_delete, \
    get_all_objects_to_add, parse_get_parameter_values_response, \
    get_object_params_to_get, get_all_param_values_to_set, \
    get_param_values_to_set, get_obj_param_values_to_set, \
    get_params_to_get, get_optional_param_to_check
from magma.enodebd.state_machines.enb_acs import EnodebAcsStateMachine
from magma.enodebd.state_machines.timer import StateMachineTimer
from magma.enodebd.tr069 import models

AcsMsgAndTransition = namedtuple(
    'AcsMsgAndTransition', ['msg', 'next_state']
)

AcsReadMsgResult = namedtuple(
    'AcsReadMsgResult', ['msg_handled', 'next_state']
)


class EnodebAcsState(ABC):
    """
    State class for the Enodeb state machine

    States can transition after reading a message from the eNB, sending a
    message out to the eNB, or when a timer completes. As such, some states
    are only responsible for message sending, and others are only responsible
    for reading incoming messages.

    In the constructor, set up state transitions.
    """
    def __init__(self):
        self._acs = None

    def enter(self) -> None:
        """
        Set up your timers here. Call transition(..) on the ACS when the timer
        completes or throw an error
        """
        pass

    def exit(self) -> None:
        """Destroy timers here"""
        pass

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        Args: message: tr069 message
        Returns: name of the next state, if transition required
        """
        raise ConfigurationError(
            '%s should implement read_msg() if it '
            'needs to handle message reading' % self.__class__.__name__)

    def get_msg(self) -> AcsMsgAndTransition:
        raise ConfigurationError(
            '%s should implement get_msg() if it '
            'needs to produce messages' % self.__class__.__name__)

    @property
    def acs(self) -> EnodebAcsStateMachine:
        return self._acs

    @acs.setter
    def acs(self, val: EnodebAcsStateMachine) -> None:
        self._acs = val

    @classmethod
    @abstractmethod
    def state_description(cls) -> str:
        """ Provide a few words about what the state represents """
        pass


class DisconnectedState(EnodebAcsState):
    """
    This state indicates that no Inform message has been received yet, or
    that no Inform message has been received for a long time.
    """
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        Args:
            message: models.Inform Tr069 Inform message
        """
        if not isinstance(message, models.Inform):
            return AcsReadMsgResult(False, None)
        process_inform_message(message, self.acs.device_name,
                               self.acs.data_model, self.acs.device_cfg)
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        """ Reply with InformResponse """
        response = models.InformResponse()
        # Set maxEnvelopes to 1, as per TR-069 spec
        response.MaxEnvelopes = 1
        return AcsMsgAndTransition(response, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Disconnected'


class UnexpectedInformState(EnodebAcsState):
    """
    This state indicates that no Inform message has been received yet, or
    that no Inform message has been received for a long time.
    """
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        Args:
            message: models.Inform Tr069 Inform message
        """
        if not isinstance(message, models.Inform):
            return AcsReadMsgResult(False, None)
        process_inform_message(message, self.acs.device_name,
                               self.acs.data_model, self.acs.device_cfg)
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        """ Reply with InformResponse """
        response = models.InformResponse()
        # Set maxEnvelopes to 1, as per TR-069 spec
        response.MaxEnvelopes = 1
        return AcsMsgAndTransition(response, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Awaiting Inform during provisioning'


class BaicellsDisconnectedState(EnodebAcsState):
    """
    This state is to handle a Baicells eNodeB issue.

    After eNodeB is rebooted, hold off configuring it for some time to give
    time for REM to run. This is a BaiCells eNodeB issue that doesn't support
    enabling the eNodeB during initial REM.
    """
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        Args:
            message: models.Inform Tr069 Inform message
        Returns:
            InformResponse
        """
        if not isinstance(message, models.Inform):
            return AcsReadMsgResult(False, None)
        process_inform_message(message, self.acs.device_name,
                               self.acs.data_model, self.acs.device_cfg)

        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        """ Returns InformResponse """
        response = models.InformResponse()
        # Set maxEnvelopes to 1, as per TR-069 spec
        response.MaxEnvelopes = 1
        return AcsMsgAndTransition(response, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Disconnected'


class BaicellsRemWaitState(EnodebAcsState):
    """
    We've already received an Inform message. This state is to handle a
    Baicells eNodeB issue.

    After eNodeB is rebooted, hold off configuring it for some time to give
    time for REM to run. This is a BaiCells eNodeB issue that doesn't support
    enabling the eNodeB during initial REM.
    """

    CONFIG_DELAY_AFTER_BOOT = 600

    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done
        self.rem_timer = None
        self.timer_handle = None

    def enter(self):
        self.rem_timer = StateMachineTimer(self.CONFIG_DELAY_AFTER_BOOT)
        def check_timer() -> None:
            if self.rem_timer.is_done():
                self.acs.transition(self.done_transition)

        self.timer_handle =\
            self.acs.event_loop.call_later(self.CONFIG_DELAY_AFTER_BOOT,
                                           check_timer)

    def exit(self):
        self.timer_handle.cancel()
        self.rem_timer = None

    def get_msg(self) -> AcsMsgAndTransition:
        return AcsMsgAndTransition(models.DummyInput(), None)

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        return AcsReadMsgResult(True, None)

    @classmethod
    def state_description(cls) -> str:
        return 'Waiting for eNB REM to run'


class WaitEmptyMessageState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        It's expected that we transition into this state right after receiving
        an Inform message and replying with an InformResponse. At that point,
        the eNB sends an empty HTTP request (aka DummyInput) to initiate the
        rest of the provisioning process
        """
        if not isinstance(message, models.DummyInput):
            return AcsReadMsgResult(False, None)
        return AcsReadMsgResult(True, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Waiting for empty message from eNodeB'


class CheckOptionalParamsState(EnodebAcsState):
    def __init__(
            self,
            acs: EnodebAcsStateMachine,
            when_done: str,
    ):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done
        self.optional_param = None

    def get_msg(self) -> AcsMsgAndTransition:
        self.optional_param = get_optional_param_to_check(self.acs.data_model)
        if self.optional_param is None:
            raise Tr069Error('Invalid State')
        # Generate the request
        request = models.GetParameterValues()
        request.ParameterNames = models.ParameterNames()
        request.ParameterNames.arrayType = 'xsd:string[1]'
        request.ParameterNames.string = []
        path = self.acs.data_model.get_parameter(self.optional_param).path
        request.ParameterNames.string.append(path)
        return AcsMsgAndTransition(request, None)

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """ Process either GetParameterValuesResponse or a Fault """
        if type(message) == models.Fault:
            self.acs.data_model.set_parameter_presence(self.optional_param,
                                                       False)
        elif type(message) == models.GetParameterValuesResponse:
            name_to_val = parse_get_parameter_values_response(
                self.acs.data_model,
                message,
            )
            logging.debug('Received CPE parameter values: %s',
                          str(name_to_val))
            for name, val in name_to_val.items():
                self.acs.data_model.set_parameter_presence(self.optional_param,
                                                           True)
                magma_val = self.acs.data_model.transform_for_magma(name, val)
                self.acs.device_cfg.set_parameter(name, magma_val)
        else:
            return AcsReadMsgResult(False, None)

        if get_optional_param_to_check(self.acs.data_model) is not None:
            return AcsReadMsgResult(True, None)
        return AcsReadMsgResult(True, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Checking if some optional parameters exist in data model'


class SendGetTransientParametersState(EnodebAcsState):
    """
    Periodically read eNodeB status. Note: keep frequency low to avoid
    backing up large numbers of read operations if enodebd is busy.
    Some eNB parameters are read only and updated by the eNB itself.
    """
    PARAMETERS = [
        ParameterName.OP_STATE,
        ParameterName.RF_TX_STATUS,
        ParameterName.GPS_STATUS,
        ParameterName.PTP_STATUS,
        ParameterName.MME_STATUS,
        ParameterName.GPS_LAT,
        ParameterName.GPS_LONG,
    ]

    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        if not isinstance(message, models.DummyInput):
            return AcsReadMsgResult(False, None)
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        request = models.GetParameterValues()
        request.ParameterNames = models.ParameterNames()
        request.ParameterNames.arrayType = \
            'xsd:string[%d]' % len(self.PARAMETERS)
        request.ParameterNames.string = []
        for name in self.PARAMETERS:
            # Not all data models have these parameters
            if self.acs.data_model.is_parameter_present(name):
                path = self.acs.data_model.get_parameter(name).path
                request.ParameterNames.string.append(path)

        return AcsMsgAndTransition(request, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Getting transient read-only parameters'


class WaitGetTransientParametersState(EnodebAcsState):
    """
    Periodically read eNodeB status. Note: keep frequency low to avoid
    backing up large numbers of read operations if enodebd is busy
    """
    def __init__(
            self,
            acs: EnodebAcsStateMachine,
            when_get: str,
            when_get_obj_params: str,
            when_delete: str,
            when_add: str,
            when_set: str,
            when_skip: str,
    ):
        super().__init__()
        self.acs = acs
        self.done_transition = when_get
        self.get_obj_params_transition = when_get_obj_params
        self.rm_obj_transition = when_delete
        self.add_obj_transition = when_add
        self.set_transition = when_set
        self.skip_transition = when_skip

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        if not isinstance(message, models.GetParameterValuesResponse):
            return AcsReadMsgResult(False, None)
        # Current values of the fetched parameters
        name_to_val = parse_get_parameter_values_response(self.acs.data_model,
                                                          message)
        logging.debug('Fetched Transient Params: %s', str(name_to_val))

        # Clear stats when eNodeB stops radiating. This is
        # because eNodeB stops sending performance metrics at this point.
        prev_rf_tx = False
        if self.acs.device_cfg.has_parameter(ParameterName.RF_TX_STATUS):
            prev_rf_tx = \
                self.acs.device_cfg.get_parameter(ParameterName.RF_TX_STATUS)
        next_rf_tx = name_to_val[ParameterName.RF_TX_STATUS]
        if prev_rf_tx is True and next_rf_tx is False:
            self.acs.stats_manager.clear_stats()

        # Update device configuration
        for name in name_to_val:
            magma_val = \
                self.acs.data_model.transform_for_magma(name,
                                                        name_to_val[name])
            self.acs.device_cfg.set_parameter(name, magma_val)

        # Update status metrics
        status = get_enodeb_status(self.acs)
        update_status_metrics(status)

        return AcsReadMsgResult(True, self.get_next_state())

    def get_next_state(self) -> str:
        should_get_params = \
            len(get_params_to_get(self.acs.device_cfg,
                                  self.acs.data_model)) > 0
        if should_get_params:
            return self.done_transition
        should_get_obj_params = \
            len(get_object_params_to_get(self.acs.desired_cfg,
                                         self.acs.device_cfg,
                                         self.acs.data_model)) > 0
        if should_get_obj_params:
            return self.get_obj_params_transition
        elif len(get_all_objects_to_delete(self.acs.desired_cfg,
                                           self.acs.device_cfg)) > 0:
            return self.rm_obj_transition
        elif len(get_all_objects_to_add(self.acs.desired_cfg,
                                        self.acs.device_cfg)) > 0:
            return self.add_obj_transition
        return self.skip_transition

    @classmethod
    def state_description(cls) -> str:
        return 'Getting transient read-only parameters'


class GetParametersState(EnodebAcsState):
    """
    Get the value of most parameters of the eNB that are defined in the data
    model. Object parameters are excluded.
    """
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        It's expected that we transition into this state right after receiving
        an Inform message and replying with an InformResponse. At that point,
        the eNB sends an empty HTTP request (aka DummyInput) to initiate the
        rest of the provisioning process
        """
        if not isinstance(message, models.DummyInput):
            return AcsReadMsgResult(False, None)
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        """
        Respond with GetParameterValuesRequest

        Get the values of all parameters defined in the data model.
        Also check which addable objects are present, and what the values of
        parameters for those objects are.
        """

        # Get the names of regular parameters
        names = get_params_to_get(self.acs.device_cfg, self.acs.data_model)

        # Generate the request
        request = models.GetParameterValues()
        request.ParameterNames = models.ParameterNames()
        request.ParameterNames.arrayType = 'xsd:string[%d]' \
                                           % len(names)
        request.ParameterNames.string = []
        for name in names:
            path = self.acs.data_model.get_parameter(name).path
            request.ParameterNames.string.append(path)

        return AcsMsgAndTransition(request, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Getting non-object parameters'


class WaitGetParametersState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """ Process GetParameterValuesResponse """
        if not isinstance(message, models.GetParameterValuesResponse):
            return AcsReadMsgResult(False, None)
        name_to_val = parse_get_parameter_values_response(self.acs.data_model,
                                                          message)
        logging.debug('Received CPE parameter values: %s', str(name_to_val))
        for name, val in name_to_val.items():
            magma_val = self.acs.data_model.transform_for_magma(name, val)
            self.acs.device_cfg.set_parameter(name, magma_val)
        return AcsReadMsgResult(True, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Getting non-object parameters'


class GetObjectParametersState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def get_msg(self) -> AcsMsgAndTransition:
        """ Respond with GetParameterValuesRequest """
        names = get_object_params_to_get(self.acs.desired_cfg,
                                         self.acs.device_cfg,
                                         self.acs.data_model)

        # Generate the request
        request = models.GetParameterValues()
        request.ParameterNames = models.ParameterNames()
        request.ParameterNames.arrayType = 'xsd:string[%d]' \
                                           % len(names)
        request.ParameterNames.string = []
        for name in names:
            path = self.acs.data_model.get_parameter(name).path
            request.ParameterNames.string.append(path)

        return AcsMsgAndTransition(request, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Getting object parameters'


class WaitGetObjectParametersState(EnodebAcsState):
    def __init__(
        self,
        acs: EnodebAcsStateMachine,
        when_delete: str,
        when_add: str,
        when_set: str,
        when_skip: str,
    ):
        super().__init__()
        self.acs = acs
        self.rm_obj_transition = when_delete
        self.add_obj_transition = when_add
        self.set_params_transition = when_set
        self.skip_transition = when_skip

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """ Process GetParameterValuesResponse """
        if not isinstance(message, models.GetParameterValuesResponse):
            return AcsReadMsgResult(False, None)

        path_to_val = {}
        for param_value_struct in message.ParameterList.ParameterValueStruct:
            path_to_val[param_value_struct.Name] = \
                param_value_struct.Value.Data
        logging.debug('Received object parameters: %s', str(path_to_val))

        # TODO: This might a string for some strange reason, investigate why
        # Get the names of parameters belonging to numbered objects
        num_plmns = \
            int(self.acs.device_cfg.get_parameter(ParameterName.NUM_PLMNS))
        for i in range(1, num_plmns + 1):
            obj_name = ParameterName.PLMN_N % i
            obj_to_params = self.acs.data_model.get_numbered_param_names()
            param_name_list = obj_to_params[obj_name]
            for name in param_name_list:
                path = self.acs.data_model.get_parameter(name).path
                value = path_to_val[path]
                magma_val = \
                    self.acs.data_model.transform_for_magma(name, value)
                self.acs.device_cfg.set_parameter_for_object(name, magma_val,
                                                             obj_name)

        # Now we can have the desired state
        if self.acs.desired_cfg is None:
            self.acs.desired_cfg = build_desired_config(
                self.acs.mconfig,
                self.acs.service_config,
                self.acs.device_cfg,
                self.acs.data_model,
                self.acs.config_postprocessor,
            )

        if len(get_all_objects_to_delete(self.acs.desired_cfg,
                                         self.acs.device_cfg)) > 0:
            return AcsReadMsgResult(True, self.rm_obj_transition)
        elif len(get_all_objects_to_add(self.acs.desired_cfg,
                                        self.acs.device_cfg)) > 0:
            return AcsReadMsgResult(True, self.add_obj_transition)
        elif len(get_all_param_values_to_set(self.acs.desired_cfg,
                                             self.acs.device_cfg,
                                             self.acs.data_model)) > 0:
            return AcsReadMsgResult(True, self.set_params_transition)
        return AcsReadMsgResult(True, self.skip_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Getting object parameters'


class DeleteObjectsState(EnodebAcsState):
    def __init__(
        self,
        acs: EnodebAcsStateMachine,
        when_add: str,
        when_skip: str,
    ):
        super().__init__()
        self.acs = acs
        self.deleted_param = None
        self.add_obj_transition = when_add
        self.skip_transition = when_skip

    def get_msg(self) -> AcsMsgAndTransition:
        """
        Send DeleteObject message to TR-069 and poll for response(s).
        Input:
            - Object name (string)
        """
        request = models.DeleteObject()
        self.deleted_param = get_all_objects_to_delete(self.acs.desired_cfg,
                                                       self.acs.device_cfg)[0]
        request.ObjectName = \
            self.acs.data_model.get_parameter(self.deleted_param).path
        return AcsMsgAndTransition(request, None)

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        Send DeleteObject message to TR-069 and poll for response(s).
        Input:
            - Object name (string)
        """
        if type(message) == models.DeleteObjectResponse:
            if message.Status != 0:
                raise Tr069Error('Received DeleteObjectResponse with '
                                 'Status=%d' % message.Status)
        elif type(message) == models.Fault:
            raise Tr069Error('Received Fault in response to DeleteObject '
                             '(faultstring = %s)' % message.FaultString)
        else:
            return AcsReadMsgResult(False, None)

        self.acs.device_cfg.delete_object(self.deleted_param)
        obj_list_to_delete = get_all_objects_to_delete(self.acs.desired_cfg,
                                                       self.acs.device_cfg)
        if len(obj_list_to_delete) > 0:
            return AcsReadMsgResult(True, None)
        if len(get_all_objects_to_add(self.acs.desired_cfg,
                                      self.acs.device_cfg)) is 0:
            return AcsReadMsgResult(True, self.skip_transition)
        return AcsReadMsgResult(True, self.add_obj_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Deleting objects'


class AddObjectsState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done
        self.added_param = None

    def get_msg(self) -> AcsMsgAndTransition:
        request = models.AddObject()
        self.added_param = get_all_objects_to_add(self.acs.desired_cfg,
                                                  self.acs.device_cfg)[0]
        request.ObjectName = \
            self.acs.data_model.get_parameter(self.added_param).path
        return AcsMsgAndTransition(request, None)

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        if type(message) == models.AddObjectResponse:
            if message.Status != 0:
                raise Tr069Error('Received AddObjectResponse with '
                                 'Status=%d' % message.Status)
        elif type(message) == models.Fault:
            raise Tr069Error('Received Fault in response to AddObject '
                             '(faultstring = %s)' % message.FaultString)
        else:
            return AcsReadMsgResult(False, None)
        instance_n = message.InstanceNumber
        self.acs.device_cfg.add_object(self.added_param % instance_n)
        obj_list_to_add = get_all_objects_to_add(self.acs.desired_cfg,
                                                 self.acs.device_cfg)
        if len(obj_list_to_add) > 0:
            return AcsReadMsgResult(True, None)
        return AcsReadMsgResult(True, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Adding objects'


class SetParameterValuesState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def get_msg(self) -> AcsMsgAndTransition:
        request = models.SetParameterValues()
        request.ParameterList = models.ParameterValueList()
        param_values = get_all_param_values_to_set(self.acs.desired_cfg,
                                                   self.acs.device_cfg,
                                                   self.acs.data_model)
        request.ParameterList.arrayType = 'cwmp:ParameterValueStruct[%d]' \
                                          % len(param_values)
        request.ParameterList.ParameterValueStruct = []
        logging.debug('Sending TR069 request to set CPE parameter values: %s',
                      str(param_values))
        for name, value in param_values.items():
            type_ = self.acs.data_model.get_parameter(name).type
            name_value = models.ParameterValueStruct()
            name_value.Value = models.anySimpleType()
            name_value.Name = self.acs.data_model.get_parameter(name).path
            enb_value = self.acs.data_model.transform_for_enb(name, value)
            if type_ in ('int', 'unsignedInt'):
                name_value.Value.type = 'xsd:%s' % type_
                name_value.Value.Data = str(enb_value)
            elif type_ == 'boolean':
                # Boolean values have integral representations in spec
                name_value.Value.type = 'xsd:boolean'
                name_value.Value.Data = str(int(enb_value))
            elif type_ == 'string':
                name_value.Value.type = 'xsd:string'
                name_value.Value.Data = str(enb_value)
            else:
                raise Tr069Error('Unsupported type for %s: %s' %
                                 (name, type_))
            request.ParameterList.ParameterValueStruct.append(name_value)

        return AcsMsgAndTransition(request, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Setting parameter values'


class SetParameterValuesNotAdminState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def get_msg(self) -> AcsMsgAndTransition:
        request = models.SetParameterValues()
        request.ParameterList = models.ParameterValueList()
        param_values = get_all_param_values_to_set(self.acs.desired_cfg,
                                                   self.acs.device_cfg,
                                                   self.acs.data_model,
                                                   exclude_admin=True)
        request.ParameterList.arrayType = 'cwmp:ParameterValueStruct[%d]' \
                                          % len(param_values)
        request.ParameterList.ParameterValueStruct = []
        logging.debug('Sending TR069 request to set CPE parameter values: %s',
                      str(param_values))
        for name, value in param_values.items():
            type_ = self.acs.data_model.get_parameter(name).type
            name_value = models.ParameterValueStruct()
            name_value.Value = models.anySimpleType()
            name_value.Name = self.acs.data_model.get_parameter(name).path
            enb_value = self.acs.data_model.transform_for_enb(name, value)
            if type_ in ('int', 'unsignedInt'):
                name_value.Value.type = 'xsd:%s' % type_
                name_value.Value.Data = str(enb_value)
            elif type_ == 'boolean':
                # Boolean values have integral representations in spec
                name_value.Value.type = 'xsd:boolean'
                name_value.Value.Data = str(int(enb_value))
            elif type_ == 'string':
                name_value.Value.type = 'xsd:string'
                name_value.Value.Data = str(enb_value)
            else:
                raise Tr069Error('Unsupported type for %s: %s' %
                                 (name, type_))
            request.ParameterList.ParameterValueStruct.append(name_value)

        return AcsMsgAndTransition(request, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Setting parameter values excluding Admin Enable'


class WaitSetParameterValuesState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        if type(message) == models.SetParameterValuesResponse:
            if message.Status != 0:
                raise Tr069Error('Received SetParameterValuesResponse with '
                                 'Status=%d' % message.Status)
            self._mark_as_configured()
            return AcsReadMsgResult(True, self.done_transition)
        elif type(message) == models.Fault:
            logging.error('Received Fault in response to SetParameterValues')
            if message.SetParameterValuesFault is not None:
                for fault in message.SetParameterValuesFault:
                    logging.error('SetParameterValuesFault Param: %s, '
                                  'Code: %s, String: %s', fault.ParameterName,
                                  fault.FaultCode, fault.FaultString)
            raise Tr069Error(
                'Received Fault in response to SetParameterValues '
                '(faultstring = %s)' % message.FaultString)
        else:
            return AcsReadMsgResult(False, None)

    def _mark_as_configured(self) -> None:
        """
        A successful attempt at setting parameter values means that we need to
        update what we think the eNB's configuration is to match what we just
        set the parameter values to.
        """
        # Values of parameters
        name_to_val = get_param_values_to_set(self.acs.desired_cfg,
                                              self.acs.device_cfg,
                                              self.acs.data_model)
        for name, val in name_to_val.items():
            magma_val = self.acs.data_model.transform_for_magma(name, val)
            self.acs.device_cfg.set_parameter(name, magma_val)

        # Values of object parameters
        obj_to_name_to_val = get_obj_param_values_to_set(self.acs.desired_cfg,
                                                         self.acs.device_cfg,
                                                         self.acs.data_model)
        for obj_name, name_to_val in obj_to_name_to_val.items():
            for name, val in name_to_val.items():
                logging.debug('Set obj: %s, name: %s, val: %s', str(obj_name),
                              str(name), str(val))
                magma_val = self.acs.data_model.transform_for_magma(name, val)
                self.acs.device_cfg.set_parameter_for_object(name, magma_val,
                                                             obj_name)
        logging.info('Successfully configured CPE parameters!')

    @classmethod
    def state_description(cls) -> str:
        return 'Setting parameter values'


class SendRebootState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        """
        This state can be transitioned into through user command.
        All messages received by enodebd will be ignored in this state.
        """
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        request = models.Reboot()
        request.CommandKey = ''
        return AcsMsgAndTransition(request, self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Rebooting eNB'


class WaitRebootResponseState(EnodebAcsState):
    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        if type(message) == models.RebootResponse:
            pass
        elif type(message) == models.Fault:
            raise Tr069Error('Received Fault in response to Reboot '
                             '(faultstring = %s)' % message.FaultString)
        else:
            return AcsReadMsgResult(False, None)
        return AcsReadMsgResult(True, self.done_transition)

    def get_msg(self) -> AcsMsgAndTransition:
        """ Reply with empty message """
        return AcsMsgAndTransition(models.DummyInput(), self.done_transition)

    @classmethod
    def state_description(cls) -> str:
        return 'Rebooting eNB'


class WaitInformMRebootState(EnodebAcsState):
    """
    After sending a reboot request, we expect an Inform request with a
    specific 'inform event code'
    """

    # Time to wait for eNodeB reboot. The measured time
    # (on BaiCells indoor eNodeB)
    # is ~110secs, so add healthy padding on top of this.
    REBOOT_TIMEOUT = 300  # In seconds
    # We expect that the Inform we receive tells us the eNB has rebooted
    INFORM_EVENT_CODE = 'M Reboot'

    def __init__(
        self,
        acs: EnodebAcsStateMachine,
        when_done: str,
        when_timeout: str,
    ):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done
        self.timeout_transition = when_timeout
        self.timeout_timer = None
        self.timer_handle = None
        self.received_inform = False

    def enter(self):
        self.timeout_timer = StateMachineTimer(self.REBOOT_TIMEOUT)

        def check_timer() -> None:
            if self.timeout_timer.is_done():
                self.acs.transition(self.timeout_transition)
                raise Tr069Error('Did not receive Inform response after '
                                 'rebooting')

        self.timer_handle = \
            self.acs.event_loop.call_later(self.REBOOT_TIMEOUT,
                                           check_timer)

    def exit(self):
        self.timer_handle.cancel()
        self.timeout_timer = None

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        if type(message) == models.Inform:
            is_correct_event = False
            for event in message.Event.EventStruct:
                logging.debug('Inform event: %s', event.EventCode)
                if event.EventCode == self.INFORM_EVENT_CODE:
                    is_correct_event = True
            if not is_correct_event:
                raise Tr069Error('Did not receive M Reboot event code in '
                                 'Inform')
        elif type(message) == models.Fault:
            # eNodeB may send faults for no apparent reason before rebooting
            return AcsReadMsgResult(True, None)
        else:
            return AcsReadMsgResult(False, None)

        self.received_inform = True
        process_inform_message(message, self.acs.device_name,
                               self.acs.data_model, self.acs.device_cfg)
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        """ Reply with InformResponse """
        if self.received_inform:
            response = models.InformResponse()
            # Set maxEnvelopes to 1, as per TR-069 spec
            response.MaxEnvelopes = 1
            return AcsMsgAndTransition(response, self.done_transition)
        else:
            return AcsMsgAndTransition(models.DummyInput(), None)

    @classmethod
    def state_description(cls) -> str:
        return 'Waiting for M Reboot code from Inform'


class WaitRebootDelayState(EnodebAcsState):
    """
    After receiving the Inform notifying us that the eNodeB has successfully
    rebooted, wait a short duration to prevent unspecified race conditions
    that may occur w.r.t reboot
    """

    # Short delay timer to prevent race conditions w.r.t. reboot
    SHORT_CONFIG_DELAY = 10

    def __init__(self, acs: EnodebAcsStateMachine, when_done: str):
        super().__init__()
        self.acs = acs
        self.done_transition = when_done
        self.config_timer = None
        self.timer_handle = None

    def enter(self):
        self.config_timer = StateMachineTimer(self.SHORT_CONFIG_DELAY)

        def check_timer() -> None:
            if self.config_timer.is_done():
                self.acs.transition(self.done_transition)

        self.timer_handle = \
            self.acs.event_loop.call_later(self.SHORT_CONFIG_DELAY,
                                           check_timer)

    def exit(self):
        self.timer_handle.cancel()
        self.config_timer = None

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        return AcsMsgAndTransition(models.DummyInput(), None)

    @classmethod
    def state_description(cls) -> str:
        return 'Waiting after eNB reboot to prevent race conditions'


class ErrorState(EnodebAcsState):
    """
    The eNB handler will enter this state when an unhandled Fault is received
    """

    def __init__(self, acs: EnodebAcsStateMachine):
        super().__init__()
        self.acs = acs

    def read_msg(self, message: Any) -> AcsReadMsgResult:
        return AcsReadMsgResult(True, None)

    def get_msg(self) -> AcsMsgAndTransition:
        return AcsMsgAndTransition(models.DummyInput(), None)

    @classmethod
    def state_description(cls) -> str:
        return 'Error state - awaiting manual reboot'
