"""Classical low-rank tensor-train classifier. A comparator, not a quantum model."""
import numpy as np
import torch

class TensorTrain(torch.nn.Module):
    def __init__(self,dimensions,classes,rank=3):
        super().__init__();self.dimensions=dimensions;self.classes=classes;self.rank=rank
        ranks=[1]+[rank]*(dimensions-1)+[classes]
        self.cores=torch.nn.ParameterList([torch.nn.Parameter(torch.randn(ranks[i],2,ranks[i+1])*.25)
                                          for i in range(dimensions)])
        self.bias=torch.nn.Parameter(torch.zeros(classes))
    def forward(self,x):
        state=torch.ones((len(x),1),device=x.device)
        for i,core in enumerate(self.cores):
            basis=torch.stack((torch.ones(len(x),device=x.device),x[:,i]),dim=1)
            state=torch.einsum('bi,ikj,bk->bj',state,core,basis)
        return state+self.bias

def fit_tensor(x,y,classes,epochs=100,rank=3):
    torch.manual_seed(20261008);torch.set_num_threads(4)
    x=torch.tensor(x,dtype=torch.float32);y=torch.tensor(y,dtype=torch.long)
    model=TensorTrain(x.shape[1],classes,rank);opt=torch.optim.Adam(model.parameters(),lr=.02)
    for _ in range(epochs):
        opt.zero_grad();loss=torch.nn.functional.cross_entropy(model(x),y);loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),2);opt.step()
    model.eval();return model,float(loss)
