"""Guard source files that local builds can see but a Git clone might omit."""
from pathlib import Path
import subprocess,unittest,tempfile,importlib.util
ROOT=Path(__file__).resolve().parents[1]
class SourceDistribution(unittest.TestCase):
 def test_swift_source_graph_is_present_and_git_tracked(self):
  required=['native-progress/Package.swift','ditnotes/native/feature_select.swift']
  required += ['native-progress/Sources/IndexerProgress/'+p for p in ['App/IndexerProgressApp.swift','Models/ProgressStatus.swift','Services/ProgressClient.swift','Views/ContentView.swift']]
  for name in required:
   self.assertTrue((ROOT/name).is_file(),name+' missing from distribution')
   if (ROOT/'.git').exists():
    result=subprocess.run(['git','ls-files','--error-unmatch','--',name],cwd=ROOT,capture_output=True,text=True)
    self.assertEqual(result.returncode,0,name+' is visible locally but absent from Git')
 def test_checkout_and_worktree_have_identical_portable_source_identity(self):
  spec=importlib.util.spec_from_file_location('source_distribution_manager',ROOT/'deploy/manager.py')
  manager=importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)
  with tempfile.TemporaryDirectory() as directory:
   checkout=Path(directory)/'ordinary checkout';checkout.mkdir();worktree=Path(directory)/'detached worktree'
   def git(*args):
    result=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','-C',str(checkout),*args],capture_output=True,text=True)
    self.assertEqual(result.returncode,0,result.stderr)
   git('init','--quiet');(checkout/'dit_notes.py').write_text('print("fixture")\n')
   git('add','dit_notes.py');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Fixture source')
   git('worktree','add','--detach','--quiet',str(worktree),'HEAD')
   self.assertTrue((checkout/'.git').is_dir());self.assertTrue((worktree/'.git').is_file())
   regular=manager.source_files(checkout);detached=manager.source_files(worktree)
   self.assertNotIn('.git',regular);self.assertNotIn('.git',detached)
   self.assertEqual(regular,detached);self.assertEqual(manager.identity(regular),manager.identity(detached))
   self.assertEqual(set(regular),{'dit_notes.py'})
 def test_git_excludes_root_model_weights_but_not_nested_swift_models(self):
  if not (ROOT/'.git').exists():self.skipTest('Archive has no Git metadata')
  ignored=subprocess.run(['git','check-ignore','--no-index','models/weights.safetensors'],cwd=ROOT,capture_output=True)
  self.assertEqual(ignored.returncode,0)
  source=subprocess.run(['git','check-ignore','--no-index','native-progress/Sources/IndexerProgress/Models/ProgressStatus.swift'],cwd=ROOT,capture_output=True)
  self.assertEqual(source.returncode,1,'Model-weight exclusion swallowed Swift source')
if __name__=='__main__':unittest.main()
