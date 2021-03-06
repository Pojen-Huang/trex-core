#!/router/bin/python
from .stl_general_test import CStlGeneral_Test, CTRexScenario
from trex_stl_lib.api import *
import os, sys

class STLRX_Test(CStlGeneral_Test):
    """Tests for RX feature"""

    def setUp(self):
        #if CTRexScenario.setup_name in ('trex08', 'trex09'):
        #    self.skip('This test makes trex08 and trex09 sick. Fix those ASAP.')
        if self.is_virt_nics:
            self.skip('Skip this for virtual NICs for now')
        per_driver_params = {"rte_vmxnet3_pmd": [1, 50, 1], "rte_ixgbe_pmd": [30, 5000, 1], "rte_i40e_pmd": [80, 5000, 1],
                             "rte_igb_pmd": [80, 500, 1], "rte_em_pmd": [1, 50, 1], "rte_virtio_pmd": [1, 50, 1]}

        CStlGeneral_Test.setUp(self)
        assert 'bi' in CTRexScenario.stl_ports_map

        self.c = CTRexScenario.stl_trex

        self.tx_port, self.rx_port = CTRexScenario.stl_ports_map['bi'][0]

        port_info = self.c.get_port_info(ports = self.rx_port)[0]
        cap = port_info['rx']['caps']
        if "flow_stats" not in cap or "latency" not in cap:
            self.skip('port {0} does not support RX'.format(self.rx_port))
        self.cap = cap

        self.rate_percent = per_driver_params[port_info['driver']][0]
        self.total_pkts = per_driver_params[port_info['driver']][1]
        if len(per_driver_params[port_info['driver']]) > 2:
            self.rate_lat = per_driver_params[port_info['driver']][2]
        else:
            self.rate_lat = self.rate_percent
        self.drops_expected = False
        self.c.reset(ports = [self.tx_port, self.rx_port])

        self.pkt = STLPktBuilder(pkt = Ether()/IP(src="16.0.0.1",dst="48.0.0.1")/UDP(dport=12,sport=1025)/('Your_paylaod_comes_here'))
        self.large_pkt = STLPktBuilder(pkt = Ether()/IP(src="16.0.0.1",dst="48.0.0.1")/UDP(dport=12,sport=1025)/('a'*1000))

    @classmethod
    def tearDownClass(cls):
        # connect back at end of tests
        if not cls.is_connected():
            CTRexScenario.stl_trex.connect()


    def __verify_flow (self, pg_id, total_pkts, pkt_len, stats):
        flow_stats = stats['flow_stats'].get(pg_id)
        latency_stats = stats['latency'].get(pg_id)

        if not flow_stats:
            assert False, "no flow stats available"

        tx_pkts  = flow_stats['tx_pkts'].get(self.tx_port, 0)
        tx_bytes = flow_stats['tx_bytes'].get(self.tx_port, 0)
        rx_pkts  = flow_stats['rx_pkts'].get(self.rx_port, 0)
        if latency_stats is not None:
            drops = latency_stats['err_cntrs']['dropped']
            ooo = latency_stats['err_cntrs']['out_of_order']
            dup = latency_stats['err_cntrs']['dup']
            sth = latency_stats['err_cntrs']['seq_too_high']
            stl = latency_stats['err_cntrs']['seq_too_low']
            lat = latency_stats['latency']
            if ooo != 0 or dup != 0 or stl != 0:
                pprint.pprint(latency_stats)
                tmp='Error packets - dropped:{0}, ooo:{1} dup:{2} seq too high:{3} seq too low:{4}'.format(drops, ooo, dup, sth, stl)
                assert False, tmp

            if (drops != 0 or sth != 0) and not self.drops_expected:
                pprint.pprint(latency_stats)
                tmp='Error packets - dropped:{0}, ooo:{1} dup:{2} seq too high:{3} seq too low:{4}'.format(drops, ooo, dup, sth, stl)
                assert False, tmp

        if tx_pkts != total_pkts:
            pprint.pprint(flow_stats)
            tmp = 'TX pkts mismatch - got: {0}, expected: {1}'.format(tx_pkts, total_pkts)
            assert False, tmp

        if tx_bytes != (total_pkts * pkt_len):
            pprint.pprint(flow_stats)
            tmp = 'TX bytes mismatch - got: {0}, expected: {1}'.format(tx_bytes, (total_pkts * pkt_len))
            assert False, tmp

        if rx_pkts != total_pkts and not self.drops_expected:
            pprint.pprint(flow_stats)
            tmp = 'RX pkts mismatch - got: {0}, expected: {1}'.format(rx_pkts, total_pkts)
            assert False, tmp

        if "rx_bytes" in self.cap:
            rx_bytes = flow_stats['rx_bytes'].get(self.rx_port, 0)
            if rx_bytes != (total_pkts * pkt_len) and not self.drops_expected:
                pprint.pprint(flow_stats)
                tmp = 'RX bytes mismatch - got: {0}, expected: {1}'.format(rx_bytes, (total_pkts * pkt_len))
                assert False, tmp


    # RX itreation
    def __rx_iteration (self, exp_list):

        self.c.clear_stats()

        self.c.start(ports = [self.tx_port])
        self.c.wait_on_traffic(ports = [self.tx_port])
        stats = self.c.get_stats()

        for exp in exp_list:
            self.__verify_flow(exp['pg_id'], exp['total_pkts'], exp['pkt_len'], stats)


    # one stream on TX --> RX
    def test_one_stream(self):
        total_pkts = self.total_pkts * 10

        try:
            s1 = STLStream(name = 'rx',
                           packet = self.pkt,
                           flow_stats = STLFlowLatencyStats(pg_id = 5),
                           mode = STLTXSingleBurst(total_pkts = total_pkts,
                                                   percentage = self.rate_lat
                                                   ))

            # add both streams to ports
            self.c.add_streams([s1], ports = [self.tx_port])

            print("\ninjecting {0} packets on port {1}\n".format(total_pkts, self.tx_port))

            exp = {'pg_id': 5, 'total_pkts': total_pkts, 'pkt_len': self.pkt.get_pkt_len()}

            self.__rx_iteration( [exp] )


        except STLError as e:
            assert False , '{0}'.format(e)


    def test_multiple_streams(self):
        num_latency_streams = 128
        num_flow_stat_streams = 127
        total_pkts = int(self.total_pkts / (num_latency_streams + num_flow_stat_streams))
        if total_pkts == 0:
            total_pkts = 1
        percent = float(self.rate_lat) / (num_latency_streams + num_flow_stat_streams)

        try:
            streams = []
            exp = []
            # 10 identical streams
            for pg_id in range(1, num_latency_streams):

                streams.append(STLStream(name = 'rx {0}'.format(pg_id),
                                         packet = self.pkt,
                                         flow_stats = STLFlowLatencyStats(pg_id = pg_id),
                                         mode = STLTXSingleBurst(total_pkts = total_pkts+pg_id, percentage = percent)))

                exp.append({'pg_id': pg_id, 'total_pkts': total_pkts+pg_id, 'pkt_len': self.pkt.get_pkt_len()})

            for pg_id in range(num_latency_streams + 1, num_latency_streams + num_flow_stat_streams):

                streams.append(STLStream(name = 'rx {0}'.format(pg_id),
                                         packet = self.pkt,
                                         flow_stats = STLFlowStats(pg_id = pg_id),
                                         mode = STLTXSingleBurst(total_pkts = total_pkts+pg_id, percentage = percent)))

                exp.append({'pg_id': pg_id, 'total_pkts': total_pkts+pg_id, 'pkt_len': self.pkt.get_pkt_len()})

            # add both streams to ports
            self.c.add_streams(streams, ports = [self.tx_port])

            self.__rx_iteration(exp)


        except STLError as e:
            assert False , '{0}'.format(e)

    def test_1_stream_many_iterations (self):
        total_pkts = self.total_pkts

        try:
            s1 = STLStream(name = 'rx',
                           packet = self.pkt,
                           flow_stats = STLFlowStats(pg_id = 5),
                           mode = STLTXSingleBurst(total_pkts = total_pkts,
                                                   percentage = self.rate_percent
                                                   ))

            s_lat = STLStream(name = 'rx',
                           packet = self.pkt,
                           flow_stats = STLFlowLatencyStats(pg_id = 5),
                           mode = STLTXSingleBurst(total_pkts = total_pkts,
                                                   percentage = self.rate_lat
                                                   ))

            print("\ninjecting {0} packets on port {1}\n".format(total_pkts, self.tx_port))

            exp = {'pg_id': 5, 'total_pkts': total_pkts, 'pkt_len': self.pkt.get_pkt_len()}
            exp_lat = {'pg_id': 5, 'total_pkts': total_pkts, 'pkt_len': self.pkt.get_pkt_len()}

            self.c.add_streams([s1], ports = [self.tx_port])
            for i in range(0, 10):
                print("starting iteration {0}".format(i))
                self.__rx_iteration( [exp] )

            self.c.remove_all_streams(ports = [self.tx_port])
            self.c.add_streams([s_lat], ports = [self.tx_port])
            for i in range(0, 10):
                print("starting iteration {0} latency".format(i))
                self.__rx_iteration( [exp_lat] )



        except STLError as e:
            assert False , '{0}'.format(e)


    
    # this test adds more and more latency streams and re-test with incremental
    def test_incremental_latency_streams (self):

        total_pkts = self.total_pkts
        percent = 0.5

        try:
            # We run till maximum streams allowed. At some point, expecting drops, because rate is too high.
            # then run with less streams again, to see that system is still working.
            for num_iter in [128, 5]:
                exp = []
                for i in range(1, num_iter):
                    # mix small and large packets
                    if i % 2 != 0:
                        my_pkt = self.pkt
                    else:
                        my_pkt = self.large_pkt
                    s1 = STLStream(name = 'rx',
                                   packet = my_pkt,
                                   flow_stats = STLFlowLatencyStats(pg_id = i),
                                   mode = STLTXSingleBurst(total_pkts = total_pkts,
                                                           percentage = percent
                                                       ))

                    # add both streams to ports
                    self.c.add_streams([s1], ports = [self.tx_port])
                    total_percent = i * percent
                    if total_percent > self.rate_lat:
                        self.drops_expected = True
                    else:
                        self.drops_expected = False

                    print("port {0} : {1} streams at {2}% of line rate\n".format(self.tx_port, i, total_percent))

                    exp.append({'pg_id': i, 'total_pkts': total_pkts, 'pkt_len': my_pkt.get_pkt_len()})

                    self.__rx_iteration( exp )

                self.c.remove_all_streams(ports = [self.tx_port])

        except STLError as e:
            assert False , '{0}'.format(e)
