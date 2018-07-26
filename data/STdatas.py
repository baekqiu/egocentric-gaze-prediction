import torch
import numpy as np
from torch.utils.data import Dataset, DataLoader
import os
from skimage import io
import math

imgPath = 'gtea_imgflow'
gtPath = 'gtea_gts'
fixsacPath = 'fixsac'
listFolders = [k for k in os.listdir(imgPath)]
listFolders.sort()
listGtFiles = [k for k in os.listdir(gtPath) if 'Alireza' not in k]
listGtFiles.sort()
listValGtFiles = [k for k in os.listdir(gtPath) if 'Alireza' in k]
listValGtFiles.sort()
print 'num of training samples: ', len(listGtFiles)

listfixsacTrain = [k for k in os.listdir(fixsacPath) if 'Alireza' not in k]
listfixsacVal = [k for k in os.listdir(fixsacPath) if 'Alireza' in k]
listfixsacVal.sort()
listfixsacTrain.sort()

imgPath_s = 'gtea_images'
listTrainFiles = [k for k in os.listdir(imgPath_s) if 'Alireza' not in k]
#listGtFiles = [k for k in os.listdir(gtPath) if 'Alireza' not in k]
listValFiles = [k for k in os.listdir(imgPath_s) if 'Alireza' in k]
#listValGtFiles = [k for k in os.listdir(gtPath) if 'Alireza' in k]
listTrainFiles.sort()
listValFiles.sort()
print 'num of val samples: ', len(listValFiles)

sparseflowpath = 'sparse_flow'
listTrainflows = [k for k in os.listdir(sparseflowpath) if 'Alireza' not in k]
listValflows = [k for k in os.listdir(sparseflowpath) if 'Alireza' in k]
listTrainflows.sort()
listValflows.sort()


def build_temporal_list(imgPath, gtPath, listFolders, listGtFiles):
    imgx = []
    imgy = []
    for gt in listGtFiles:
        folder = gt[:-17]
        assert(folder in listFolders)
        number = gt[-9:-4]  #is a string
        xstr = []
        ystr = []
        for m in range(10):
            xstr.append(imgPath + '/' + folder + '/' + 'flow_x_' + '%05d'%(int(number) - m) + '.jpg')
            ystr.append(imgPath + '/' + folder + '/' + 'flow_y_' + '%05d'%(int(number) - m) + '.jpg')
        imgx.append(xstr)
        imgy.append(ystr)
    return imgx, imgy

class STDatasetTrain(Dataset):
    def __init__(self, imgPath, imgPath_s, gtPath, sflowPath, listFolders, listTrainFiles, listGtFiles, listfixsacTrain, listTrainflows, transform = None):
        #imgPath is flow path, containing several subfolders
        self.listFolders = listFolders
        self.listGtFiles = listGtFiles
        self.transform = transform
        self.imgPath = imgPath
        self.imgPath_s = imgPath_s
        self.listTrainFiles = listTrainFiles
        self.sflowPath = sflowPath
        self.listTrainflows = listTrainflows
        self.gtPath = gtPath
        self.imgx, self.imgy = build_temporal_list(imgPath, gtPath, self.listFolders, listGtFiles)
        self.fixsac = 'i'
        self.flow = listTrainflows
        for file in listfixsacTrain:
            a=np.loadtxt('fixsac/'+file)
            ker = np.array([1,1,1])
            a = np.convolve(a, ker)
            a = a[1:-1]
            a = (a>0).astype(float)
            if type(self.fixsac)==type('i'):
                self.fixsac = a
            else:
                self.fixsac = np.concatenate((self.fixsac,a))
    
    def __len__(self):
        return len(self.listGtFiles)

    def __getitem__(self, index):
        im = io.imread(self.imgPath_s + '/' + self.listTrainFiles[index])
        im = im.transpose((2,0,1))
        im = torch.from_numpy(im)
        im = im.float().div(255)
        im = im.sub_(torch.FloatTensor([0.485,0.456,0.406]).view(3,1,1)).div_(torch.FloatTensor([0.229,0.224,0.225]).view(3,1,1))
        flowx = self.imgx[index]
        flowy = self.imgy[index]
        flowarr = np.zeros((224,224,20))
        for flowi in range(10):
            currflowx = io.imread(flowx[flowi])
            currflowy = io.imread(flowy[flowi])
            flowarr[:,:,2*flowi] = currflowx
            flowarr[:,:,2*flowi+1] = currflowy
        gt = io.imread(self.gtPath + '/' + self.listGtFiles[index])
        #flowarr = np.divide(flowarr, 255.0)
        flowarr = np.subtract(flowarr, 0.5)
        flowarr = np.divide(flowarr, 0.5)
        flowarr = flowarr.transpose((2,0,1))
        flowarr = torch.from_numpy(flowarr)
        gt = torch.from_numpy(gt)
        gt = gt.float().div(255)
        gt = gt.unsqueeze(0)
        flowmean = np.load(self.sflowPath + '/' + self.listTrainflows[index])
        flowmean = torch.FloatTensor([float(flowmean)])
        sample = {'image': im, 'flow': flowarr, 'gt': gt, 'fixsac': torch.FloatTensor([self.fixsac[index]]), 'imname': self.listTrainFiles[index], 'flowmean': flowmean}
        if self.transform:
            sample = self.transform(sample)
        return sample

class STDatasetVal(Dataset):
    def __init__(self, imgPath, imgPath_s, gtPath, sflowPath, listFolders, listValFiles, listValGtFiles, listfixsacVal, listValflows, transform = None):
        self.listFolders = listFolders
        self.listGtFiles = listValGtFiles
        self.listValFiles = listValFiles
        self.transform = transform
        self.imgPath = imgPath
        self.imgPath_s = imgPath_s
        self.gtPath = gtPath
        self.listValflows = listValflows
        self.sflowPath = sflowPath
        self.imgx, self.imgy = build_temporal_list(imgPath, gtPath, self.listFolders, listGtFiles)
        self.fixsac = 'i'
        for file in listfixsacVal:
            a=np.loadtxt('fixsac/'+file)
            ker = np.array([1,1,1])
            a = np.convolve(a, ker)
            a = a[1:-1]
            a = (a>0).astype(float)
            if type(self.fixsac) == type('i'):
                self.fixsac = a
            else:
                self.fixsac = np.concatenate((self.fixsac, a))

    def __len__(self):
        return len(self.listGtFiles)

    def __getitem__(self, index):
        im = io.imread(self.imgPath_s + '/' + self.listValFiles[index])
        im = im.transpose((2,0,1))
        im = torch.from_numpy(im)
        im = im.float().div(255)
        im = im.sub_(torch.FloatTensor([0.485,0.456,0.406]).view(3,1,1)).div_(torch.FloatTensor([0.229,0.224,0.225]).view(3,1,1))
        flowx = self.imgx[index]
        flowy = self.imgy[index]
        flowarr = np.zeros((224,224,20))
        for flowi in range(10):
            currflowx = io.imread(flowx[flowi])
            currflowy = io.imread(flowy[flowi])
            flowarr[:,:,2*flowi] = currflowx
            flowarr[:,:,2*flowi+1] = currflowy
        gt = io.imread(self.gtPath + '/' + self.listGtFiles[index])
        #flowarr = np.divide(flowarr, 255.0)
        flowarr = np.subtract(flowarr, 0.5)
        flowarr = np.divide(flowarr, 0.5)
        flowarr = flowarr.transpose((2,0,1))
        flowarr = torch.from_numpy(flowarr)
        gt = torch.from_numpy(gt)
        gt = gt.float().div(255)
        gt = gt.unsqueeze(0)
        flowmean = np.load(self.sflowPath + '/' + self.listValflows[index])
        flowmean = torch.FloatTensor([float(flowmean)])
        sample = {'image': im, 'flow': flowarr, 'gt': gt, 'fixsac': torch.FloatTensor([self.fixsac[index]]), 'imname': self.listValFiles[index], 'flowmean': flowmean}
        if self.transform:
            sample = self.transform(sample)
        return sample


STTrainData = STDatasetTrain('gtea_imgflow', 'gtea_images', 'gtea_gts', 'sparse_flow', listFolders, listTrainFiles, listGtFiles, listfixsacTrain, listTrainflows)
#STTrainLoader = DataLoader(dataset=STTrainData, batch_size=10, shuffle=False, num_workers=1, pin_memory=True)

STValData = STDatasetVal('gtea_imgflow', 'gtea_images', 'gtea_gts', 'sparse_flow', listFolders, listValFiles, listValGtFiles, listfixsacVal, listValflows)
#STValLoader = DataLoader(dataset=STValData, batch_size=10, shuffle=False, num_workers=1, pin_memory=True)

if __name__ == '__main__':
    STValLoader = DataLoader(dataset=STValData, batch_size=1, shuffle=False, num_workers=1, pin_memory=True)
    print len(STValLoader)