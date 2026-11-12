import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from ditnotes.backup_volumes import mac_identity


class BackupVolumes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.mount=Path(self.temp.name).resolve()
        self.info={'VolumeUUID':'volume-one','MountPoint':str(self.mount),'FilesystemType':'apfs','DeviceIdentifier':'disk7s1','APFSContainerReference':'disk7','APFSPhysicalStores':[{'APFSPhysicalStore':'disk2s2'}]}
        self.responses={('info','-plist',str(self.mount)):self.info,('info','-plist','disk2s2'):{'DeviceIdentifier':'disk2s2','ParentWholeDisk':'disk2','WholeDisk':False},('info','-plist','disk2'):{'DeviceIdentifier':'disk2','WholeDisk':True,'BusProtocol':'USB','VirtualOrPhysical':'Physical','MediaUUID':'disk-two'}}
    def runner(self,args,**kwargs):
        response=self.responses.get(tuple(args[1:]))
        if response is None:return subprocess.CompletedProcess(args,1,b'',b'not found')
        return subprocess.CompletedProcess(args,0,plistlib.dumps(response),b'')
    def test_apfs_resolves_physical_whole_disk(self):
        identity=mac_identity(self.mount,self.runner)
        self.assertEqual(identity,{'uuid':'VOLUME-ONE','mount':str(self.mount),'filesystem':'apfs','physical':['disk:disk2','mediauuid:DISK-TWO']})
    def test_separate_apfs_volumes_on_same_device_overlap(self):
        first=mac_identity(self.mount,self.runner);self.info['VolumeUUID']='other';self.info['DeviceIdentifier']='disk7s2'
        second=mac_identity(self.mount,self.runner)
        self.assertNotEqual(first['uuid'],second['uuid']);self.assertTrue(set(first['physical'])&set(second['physical']))
    def test_apfs_container_fallback(self):
        del self.info['APFSPhysicalStores']
        self.responses[('apfs','list','-plist')]={'Containers':[{'ContainerReference':'disk7','PhysicalStores':[{'DeviceIdentifier':'disk2s2'}]}]}
        self.assertIn('disk:disk2',mac_identity(self.mount,self.runner)['physical'])
    def test_unknown_container_fails_closed(self):
        del self.info['APFSPhysicalStores'];self.responses[('apfs','list','-plist')]={'Containers':[]}
        with self.assertRaises(RuntimeError):mac_identity(self.mount,self.runner)
    def test_virtual_or_raid_is_not_independent_physical_device(self):
        for field,value in [('VirtualOrPhysical','Virtual'),('RAIDMaster',True),('BusProtocol','')]:
            with self.subTest(field=field):
                original=dict(self.responses[('info','-plist','disk2')]);self.responses[('info','-plist','disk2')][field]=value
                with self.assertRaises(RuntimeError):mac_identity(self.mount,self.runner)
                self.responses[('info','-plist','disk2')]=original
    def test_non_apfs_partition_resolves_parent(self):
        self.info.update(FilesystemType='exfat',DeviceIdentifier='disk2s2',ParentWholeDisk='disk2')
        self.assertIn('disk:disk2',mac_identity(self.mount,self.runner)['physical'])
    def test_network_or_missing_uuid_fails_closed(self):
        self.info.pop('VolumeUUID')
        with self.assertRaises(RuntimeError):mac_identity(self.mount,self.runner)
    def test_fusion_records_both_backing_devices(self):
        self.info['APFSPhysicalStores'].append({'APFSPhysicalStore':'disk4s2'})
        self.responses[('info','-plist','disk4s2')]={'DeviceIdentifier':'disk4s2','ParentWholeDisk':'disk4','WholeDisk':False}
        self.responses[('info','-plist','disk4')]={'DeviceIdentifier':'disk4','WholeDisk':True,'BusProtocol':'SATA'}
        self.assertIn('disk:disk4',mac_identity(self.mount,self.runner)['physical'])
    def test_timeout_and_malformed_response_fail(self):
        def timeout(*a,**kw):raise subprocess.TimeoutExpired(a[0],kw['timeout'])
        with self.assertRaises(RuntimeError):mac_identity(self.mount,timeout,timeout=.01)
        def malformed(*a,**kw):return subprocess.CompletedProcess(a[0],0,b'garbage',b'')
        with self.assertRaises(RuntimeError):mac_identity(self.mount,malformed)
    def test_missing_path_no_creation(self):
        target=self.mount/'absent'
        with self.assertRaises(FileNotFoundError):mac_identity(target,self.runner)
        self.assertFalse(target.exists())

if __name__=='__main__':unittest.main()


class SoftRAIDVolumes(unittest.TestCase):
    runner=BackupVolumes.runner
    def setUp(self):
        BackupVolumes.setUp(self)
        self.info['APFSPhysicalStores']=[{'APFSPhysicalStore':'disk22'}]
        self.responses[('info','-plist','disk22')]={'DeviceIdentifier':'disk22','WholeDisk':True,'VirtualOrPhysical':'Virtual','IORegistryEntryName':'SoftRAID_Media','DiskUUID':'RAID-UUID'}
        self.registry={'BSD Name':'disk22','IOObjectClass':'com_softraid_driver_SoftRAID_Media','UUID':'RAID-UUID','SoftRAID Provider Array':['disk2s2']}
        self.responses[('-a','-r','-c','com_softraid_driver_SoftRAID_Media')]=[self.registry]
    def test_raid_expands_explicit_provider_mapping(self):
        self.assertEqual(mac_identity(self.mount,self.runner)['physical'],['disk:disk2','mediauuid:DISK-TWO','softraid:RAID-UUID'])
    def test_registry_uuid_mismatch_fails(self):
        self.registry['UUID']='OTHER'
        with self.assertRaises(RuntimeError):mac_identity(self.mount,self.runner)
    def test_missing_or_ambiguous_mapping_fails(self):
        for rows in [[],[self.registry,self.registry]]:
            with self.subTest(rows=len(rows)):
                self.responses[('-a','-r','-c','com_softraid_driver_SoftRAID_Media')]=rows
                with self.assertRaises(RuntimeError):mac_identity(self.mount,self.runner)
    def test_cyclic_or_malformed_providers_fail(self):
        for providers in [['disk22'],[],['../../disk2'],['disk2s2','disk2s2']]:
            with self.subTest(providers=providers):
                self.registry['SoftRAID Provider Array']=providers
                with self.assertRaises(RuntimeError):mac_identity(self.mount,self.runner)
    def test_same_backing_member_detected_across_raid_and_regular_volume(self):
        raid=mac_identity(self.mount,self.runner)
        self.info['APFSPhysicalStores']=[{'APFSPhysicalStore':'disk2s2'}];self.info['VolumeUUID']='OTHER'
        plain=mac_identity(self.mount,self.runner)
        self.assertTrue(set(raid['physical'])&set(plain['physical']))
