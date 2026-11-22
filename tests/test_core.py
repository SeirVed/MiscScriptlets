import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xyz_resume_core import *

class CoreTests(unittest.TestCase):
    def test_csv(self):
        self.assertEqual(csv_values('DPM++ 2M,"Euler, A",Euler a'),['DPM++ 2M','Euler, A','Euler a'])
    def test_checkpoint_and_assembly(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            axes={'x':{'type':'Sampler','values':['Euler','DPM++ 2M','LCM']},
                  'y':{'type':'Schedule type','values':['Karras','Normal']},
                  'z':{'type':'Nothing','values':[0]}}
            cfg={'model':'test','prompt':'test','axes':axes}
            manifest=new_manifest(cfg,axes,111,222)
            atomic_json(folder/'manifest.json',manifest)
            self.assertEqual(load_manifest(folder)['signature'],signature(cfg))
            for ix in range(3):
                for iy in range(2):
                    im=Image.new('RGB',(110,70),(ix*85,iy*100,140))
                    atomic_png(cell_path(folder,ix,iy,0),im,'test parameters',{'Resume XYZ Signature':manifest['signature'],'Resume XYZ Coordinates':f'{ix},{iy},0'})
            self.assertEqual(saved_count(folder,manifest),6)
            self.assertFalse(valid_cell(cell_path(folder,0,0,0),'wrong',0,0,0))
            viewer=generate_viewer(folder,manifest)
            page=viewer.read_text('utf-8')
            self.assertIn('6 / 6 saved',page)
            self.assertIn('Schedule type',page)
            self.assertIn('DPM++ 2M',page)
            self.assertIn('loading="lazy"',page)
            previews=build_previews(folder,manifest)
            self.assertEqual(len(previews),1)
            with Image.open(previews[0]) as im:self.assertGreater(im.width,100)
            tiles=build_tiles(folder,manifest)
            self.assertEqual(len(tiles),1)
            with Image.open(tiles[0]) as im:self.assertEqual(im.size,(330,140))
            self.assertTrue((folder/'tiles'/'tiles.json').is_file())
            # Crash-safe marker: missing or wrong metadata => treated as pending
            with Image.open(cell_path(folder,2,1,0)) as im:temp=im.copy()
            temp.save(cell_path(folder,2,1,0),'PNG')
            self.assertEqual(saved_count(folder,manifest),5)
    def test_name(self):
        self.assertEqual(safe_name('../bad/name'), 'bad_name')
        self.assertNotEqual(signature({'a':2}),signature({'a':1}))

if __name__=='__main__':unittest.main()