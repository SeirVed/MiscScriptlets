"""API-contract smoke test with lightweight Forge fakes; NOT a substitute for real Forge Neo."""
import importlib.util
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

EXT = Path(__file__).resolve().parents[1]

class StubIntegration(unittest.TestCase):
    def test_resume_after_crash_without_rerendering(self):
        with tempfile.TemporaryDirectory() as temp:
            native_path = Path(temp)/'scripts'/'xyz_grid.py'
            native_path.parent.mkdir(parents=True)
            native_path.write_text('',encoding='utf-8')
            mods = types.ModuleType('modules');mods.__path__=[]
            paths = types.ModuleType('modules.paths');paths.data_path=temp;paths.script_path=temp
            processing = types.ModuleType('modules.processing')
            def fix_seed(p):
                if p.seed == -1:p.seed=472399
                if p.subseed == -1:p.subseed=1001
            processing.fix_seed=fix_seed
            calls=[]
            def process_images(p):
                calls.append((p.sampler_name,p.scheduler,p.seed))
                return types.SimpleNamespace(images=[Image.new('RGB',(32,24),'red')], index_of_first_image=0,infotexts=['test generated metadata'],info='test generated metadata')
            processing.process_images=process_images
            class Processed:
                def __init__(self,p,imgs,seed=-1,info='',infotexts=None):
                    self.images=imgs;self.info=info;self.infotexts=infotexts
            processing.Processed=Processed
            shared=types.ModuleType('modules.shared')
            shared.state=types.SimpleNamespace(interrupted=False,stopping_generation=False,job_count=0,job='')
            shared.opts=types.SimpleNamespace(sd_model_checkpoint='test',forge_additional_modules=[],CLIP_stop_at_last_layers=1)
            callbacks=types.ModuleType('modules.script_callbacks');callbacks.on_app_started=lambda fn:None
            script_loading=types.ModuleType('modules.script_loading')
            class AxisOption:
                def __init__(self,label,typ,apply): self.label=label;self.type=typ;self.apply=apply;self.prepare=None;self.confirm=None;self.choices=None
            class AxisOptionTxt2Img(AxisOption):
                is_img2img=False
            nothing=AxisOption('Nothing',str,lambda p,v,vs:None)
            sampler=AxisOptionTxt2Img('Sampler',str,lambda p,v,vs:setattr(p,'sampler_name',v))
            schedule=AxisOption('Schedule type',str,lambda p,v,vs:setattr(p,'scheduler',v))
            native=types.SimpleNamespace(AxisOption=AxisOption,axis_options=[nothing,sampler,schedule],str_permutations=lambda x:x,
                                         re_range=re.compile('x'),re_range_count=re.compile('x'),re_range_float=re.compile('x'),re_range_count_float=re.compile('x'),
                                         refresh_loading_params_for_xyz_grid=lambda:None)
            script_loading.loaded_scripts={str(native_path):native}
            scripts=types.ModuleType('modules.scripts');scripts.Script=type('Script',(),{})
            mods.paths=paths;mods.processing=processing;mods.script_loading=script_loading
            mods.scripts=scripts;mods.script_callbacks=callbacks;mods.shared=shared
            with patch.dict(sys.modules,{'modules':mods,'modules.paths':paths,'modules.processing':processing,'modules.shared':shared,
                'modules.scripts':scripts,'modules.script_callbacks':callbacks,'modules.script_loading':script_loading}):
                spec=importlib.util.spec_from_file_location('_stub_test_resumable',EXT/'scripts'/'resumable_xyz.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                class P:
                    def __init__(self):
                        self.batch_size=1;self.n_iter=1;self.prompt='test prompt';self.negative_prompt='';self.seed=-1;self.subseed=-1
                        self.styles=[];self.override_settings={};self.extra_generation_params={};self.width=32;self.height=24
                        self.steps=10;self.cfg_scale=5;self.sampler_name='Euler';self.scheduler='Normal'
                s=module.Script();s._is_img2img=False
                def call():
                    return s.run(P(),'Sampler','Euler,Euler a','Schedule type','Normal,Karras','Nothing','',
                                 'integration-test',False,False,True,False)
                a=call()
                self.assertIn('4/4',a.info)
                self.assertEqual(len(calls),4)
                b=call()
                self.assertIn('4/4',b.info)
                self.assertEqual(len(calls),4)
                from xyz_resume_core import cell_path
                cell_path(module.JOBS_DIR/'integration-test',1,0,0).unlink()
                c=call()
                self.assertEqual(len(calls),5)
                self.assertIn('4/4',c.info)
                self.assertTrue((module.JOBS_DIR/'integration-test'/'viewer.html').is_file())
                self.assertTrue((module.JOBS_DIR/'integration-test'/'thumbs'/'z0000_y00000_x00000.jpg').is_file())

if __name__=='__main__':unittest.main()